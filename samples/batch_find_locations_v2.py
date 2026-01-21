#!/usr/bin/env python3
"""
Batch find person locations from CSV with throttling, retries, and checkpointing.

This script processes a CSV with people's names and colleges, finds their LinkedIn
profiles, extracts locations, and saves results to a new CSV. Includes:
- Single BrowserManager instance (no duplicate browser/session opens)
- Per-row throttling with jitter to avoid rate limits
- Exponential backoff with jitter on transient errors
- Batch-wise CSV flushing (default every 10 rows)
- Checkpoint/resume support for large runs
- CLI options for customization

Usage:
    python samples/batch_find_locations_v2.py \\
        --input samples/input_people.csv \\
        --output samples/output_locations.csv \\
        --batch-size 10 \\
        --base-delay 3 \\
        --jitter 2 \\
        --max-attempts 3 \\
        --resume \\
        --dry-run
"""
import asyncio
import csv
import json
import logging
import random
import sys
from argparse import ArgumentParser
from pathlib import Path
from typing import Optional, Tuple

from linkedin_scraper.core.browser import BrowserManager
from find_person_location import find_person_location_on_page

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class BatchProcessorCheckpoint:
    """Manages checkpoint/progress state for batch processing."""
    
    def __init__(self, checkpoint_file: str):
        self.checkpoint_file = checkpoint_file
        self.data = self._load()
    
    def _load(self) -> dict:
        """Load checkpoint from file if it exists."""
        if Path(self.checkpoint_file).exists():
            try:
                with open(self.checkpoint_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load checkpoint: {e}")
                return {'last_index': -1, 'rows_written': 0}
        return {'last_index': -1, 'rows_written': 0}
    
    def save(self, last_index: int, rows_written: int):
        """Save checkpoint atomically."""
        try:
            temp_file = f"{self.checkpoint_file}.tmp"
            with open(temp_file, 'w') as f:
                json.dump({'last_index': last_index, 'rows_written': rows_written}, f)
            Path(temp_file).replace(self.checkpoint_file)
        except Exception as e:
            logger.error(f"Failed to save checkpoint: {e}")
    
    def get_start_index(self) -> int:
        """Get index to resume from (skip already processed rows)."""
        return self.data.get('last_index', -1) + 1
    
    def remove(self):
        """Remove checkpoint file on success."""
        try:
            Path(self.checkpoint_file).unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Failed to remove checkpoint: {e}")


async def with_exponential_backoff(
    coro_func,
    max_attempts: int = 3,
    base_delay: float = 2.0,
    jitter: float = 1.0,
):
    """
    Execute an async function with exponential backoff and jitter on failure.
    
    Args:
        coro_func: Async callable that returns (success, result) or raises
        max_attempts: Maximum attempts before giving up
        base_delay: Base delay in seconds
        jitter: Jitter variance in seconds
        
    Returns:
        Result from coro_func or (None, error_msg) on all attempts exhausted
    """
    last_error = None
    
    for attempt in range(max_attempts):
        try:
            result = await coro_func()
            return result
        except Exception as e:
            last_error = str(e)
            if attempt < max_attempts - 1:
                delay = (base_delay * (2 ** attempt)) + random.uniform(0, jitter)
                logger.debug(f"Attempt {attempt + 1} failed, retrying in {delay:.2f}s: {e}")
                await asyncio.sleep(delay)
            else:
                logger.debug(f"All {max_attempts} attempts failed: {e}")
    
    return None, f"Failed after {max_attempts} attempts: {last_error}"


async def process_csv(
    input_file: str,
    output_file: str,
    session_file: str = "linkedin_session.json",
    batch_size: int = 10,
    base_delay: float = 3.0,
    jitter: float = 2.0,
    max_attempts: int = 3,
    resume: bool = False,
    dry_run: bool = False,
):
    """
    Process CSV file with names and colleges, extract locations.
    
    Args:
        input_file: Path to input CSV (columns: name, college)
        output_file: Path to output CSV (columns: name, college, profile_url, location)
        session_file: LinkedIn session file to load
        batch_size: Flush results every N rows
        base_delay: Base throttle delay in seconds
        jitter: Jitter variance in seconds
        max_attempts: Max retry attempts per row
        resume: Resume from checkpoint if it exists
        dry_run: Only search, don't navigate to profiles
    """
    # Load input CSV
    if not Path(input_file).exists():
        logger.error(f"Input file not found: {input_file}")
        return
    
    rows = []
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except Exception as e:
        logger.error(f"Error reading input CSV: {e}")
        return
    
    if not rows:
        logger.error("Input CSV is empty")
        return
    
    logger.info(f"📂 Found {len(rows)} rows in input CSV")
    logger.info(f"📝 Output: {output_file}")
    logger.info(f"⏱️  Throttle: base_delay={base_delay}s, jitter={jitter}s, max_attempts={max_attempts}")
    
    # Checkpoint management
    checkpoint_file = f"{output_file}.progress.json"
    checkpoint = BatchProcessorCheckpoint(checkpoint_file)
    start_index = checkpoint.get_start_index() if resume else 0
    
    if start_index > 0:
        logger.info(f"🔄 Resuming from row {start_index + 1} (last completed: {checkpoint.data.get('last_index', -1) + 1})")
    
    # Open browser once for all lookups
    async with BrowserManager(headless=True) as browser:
        # Load existing session
        try:
            await browser.load_session(session_file)
            logger.info("✓ Session loaded")
        except Exception as e:
            logger.error(f"Failed to load session: {e}")
            return
        
        results = []
        error_rows = []
        
        # Process rows
        for idx in range(start_index, len(rows)):
            row = rows[idx]
            name = row.get('name', '').strip()
            college = row.get('college', '').strip()
            
            if not name or not college:
                logger.warning(f"[{idx + 1}/{len(rows)}] ⚠️  Skipping - missing name or college")
                error_rows.append({'name': name, 'college': college, 'error': 'Missing data'})
                continue
            
            logger.info(f"[{idx + 1}/{len(rows)}] 🔍 {name} | {college}")
            
            # Define async function for retry logic
            async def lookup():
                if dry_run:
                    # In dry-run, just search and return first profile link
                    search_query = f"{name} {college}"
                    search_url = f"https://www.linkedin.com/search/results/people/?keywords={search_query.replace(' ', '%20')}"
                    await browser.page.goto(search_url, wait_until='domcontentloaded')
                    await asyncio.sleep(0.5)
                    profile_links = await browser.page.locator('a[href*="/in/"]').all()
                    if profile_links:
                        profile_url = await profile_links[0].get_attribute('href')
                        if not profile_url.startswith('http'):
                            profile_url = f"https://www.linkedin.com{profile_url}"
                        return profile_url, "DRY_RUN"
                    else:
                        return None, "No profile found (dry-run)"
                else:
                    # Normal mode: search and scrape location
                    return await find_person_location_on_page(browser.page, name, college)
            
            # Retry with exponential backoff
            profile_url, location = await with_exponential_backoff(
                lookup,
                max_attempts=max_attempts,
                base_delay=base_delay,
                jitter=jitter,
            )
            
            if profile_url:
                logger.info(f"     ✓ {profile_url}")
                logger.info(f"     📍 {location}")
                results.append({
                    'name': name,
                    'college': college,
                    'profile_url': profile_url,
                    'location': location,
                })
            else:
                logger.info(f"     ❌ {location}")
                error_rows.append({'name': name, 'college': college, 'error': location})
            
            # Flush results every batch_size rows
            if len(results) >= batch_size:
                _flush_csv(output_file, results, idx)
                checkpoint.save(idx, len(results))
                results = []
            
            # Throttle between requests
            await asyncio.sleep(base_delay + random.uniform(0, jitter))
        
        # Flush remaining results
        if results:
            _flush_csv(output_file, results, len(rows) - 1)
        
        # Save error log if any
        if error_rows:
            error_file = f"{output_file}.errors.csv"
            _write_csv(error_file, error_rows, ['name', 'college', 'error'])
            logger.info(f"❌ Errors saved to: {error_file}")
    
    # Cleanup checkpoint on success
    checkpoint.remove()
    
    # Summary
    logger.info("\n" + "="*60)
    logger.info(f"✅ Batch processing complete")
    logger.info(f"   Output: {output_file}")
    if error_rows:
        logger.info(f"   Errors: {error_file}")
    logger.info("="*60 + "\n")


def _flush_csv(output_file: str, results: list, current_idx: int):
    """Append results to CSV file."""
    try:
        file_exists = Path(output_file).exists()
        with open(output_file, 'a', newline='', encoding='utf-8') as f:
            fieldnames = ['name', 'college', 'profile_url', 'location']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerows(results)
        logger.debug(f"Flushed {len(results)} rows to CSV (processed up to row {current_idx + 1})")
    except Exception as e:
        logger.error(f"Error writing CSV: {e}")


def _write_csv(output_file: str, rows: list, fieldnames: list):
    """Write rows to CSV file."""
    try:
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    except Exception as e:
        logger.error(f"Error writing CSV: {e}")


def main():
    """CLI entry point."""
    parser = ArgumentParser(description="Batch find person locations on LinkedIn with throttling and checkpointing")
    parser.add_argument(
        '--input',
        type=str,
        default='samples/input_people.csv',
        help='Input CSV file (columns: name, college)'
    )
    parser.add_argument(
        '--output',
        type=str,
        default='samples/output_locations.csv',
        help='Output CSV file'
    )
    parser.add_argument(
        '--session',
        type=str,
        default='linkedin_session.json',
        help='LinkedIn session file to load'
    )
    parser.add_argument(
        '--batch-size',
        type=int,
        default=10,
        help='Flush results to CSV every N rows (default: 10)'
    )
    parser.add_argument(
        '--base-delay',
        type=float,
        default=3.0,
        help='Base throttle delay in seconds (default: 3.0)'
    )
    parser.add_argument(
        '--jitter',
        type=float,
        default=2.0,
        help='Jitter variance in seconds (default: 2.0)'
    )
    parser.add_argument(
        '--max-attempts',
        type=int,
        default=3,
        help='Max retry attempts per row (default: 3)'
    )
    parser.add_argument(
        '--resume',
        action='store_true',
        help='Resume from checkpoint if it exists'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Only search, don\'t navigate to profiles'
    )
    
    args = parser.parse_args()
    
    # Validate input
    if not Path(args.input).exists():
        logger.error(f"Input file not found: {args.input}")
        sys.exit(1)
    
    # Run batch processing
    try:
        asyncio.run(
            process_csv(
                input_file=args.input,
                output_file=args.output,
                session_file=args.session,
                batch_size=args.batch_size,
                base_delay=args.base_delay,
                jitter=args.jitter,
                max_attempts=args.max_attempts,
                resume=args.resume,
                dry_run=args.dry_run,
            )
        )
    except KeyboardInterrupt:
        logger.info("\n⏹️  Batch processing interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Batch processing failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
