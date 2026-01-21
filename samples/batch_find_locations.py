#!/usr/bin/env python3
"""
Batch find person locations from CSV

This script takes a CSV file with people's names and colleges,
finds their LinkedIn profiles, extracts locations, and saves results to a new CSV.

This is a wrapper around find_person_location.py for batch processing.
"""
import asyncio
import csv
from pathlib import Path
from find_person_location import find_person_location
from linkedin_scraper.core.browser import BrowserManager


async def process_csv(input_file: str, output_file: str):
    """
    Process CSV file with names and colleges, extract locations.
    
    Wrapper function that uses find_person_location for each row.
    
    Args:
        input_file: Path to input CSV (columns: name, college)
        output_file: Path to output CSV (columns: name, college, profile_url, location)
    """
    # Read input CSV
    if not Path(input_file).exists():
        print(f"❌ Input file not found: {input_file}")
        return
    
    rows = []
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except Exception as e:
        print(f"❌ Error reading input CSV: {e}")
        return
    
    if not rows:
        print("❌ Input CSV is empty")
        return
    
    print(f"📂 Found {len(rows)} rows in input CSV")
    print(f"📝 Processing and saving to: {output_file}\n")
    
    # Open browser once for all lookups
    async with BrowserManager(headless=False) as browser:
        # Load existing session
        await browser.load_session("linkedin_session.json")
        print("✓ Session loaded\n")
        
        results = []
        
        for idx, row in enumerate(rows, 1):
            name = row.get('name', '').strip()
            college = row.get('college', '').strip()
            
            if not name or not college:
                print(f"⚠️  Row {idx}: Skipping - missing name or college")
                results.append({
                    'name': name,
                    'college': college,
                    'profile_url': 'SKIPPED',
                    'location': 'Missing data'
                })
                continue
            
            print(f"[{idx}/{len(rows)}] 🔍 Searching: {name} | {college}")
            
            # Use find_person_location wrapper
            profile_url, location = await find_person_location(name, college)
            
            if profile_url:
                print(f"     ✓ Found: {profile_url}")
                print(f"     📍 Location: {location}\n")
            else:
                print(f"     ❌ Not found: {location}\n")
            
            results.append({
                'name': name,
                'college': college,
                'profile_url': profile_url if profile_url else '',
                'location': location
            })
    
    # Write output CSV
    try:
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            fieldnames = ['name', 'college', 'profile_url', 'location']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        
        print("="*60)
        print(f"✅ Results saved to: {output_file}")
        print("="*60)
        
        # Print summary
        found_count = sum(1 for r in results if r['profile_url'] and r['profile_url'] != 'SKIPPED')
        print(f"\n📊 Summary:")
        print(f"   Total processed: {len(rows)}")
        print(f"   Profiles found: {found_count}")
        print(f"   Not found: {len(rows) - found_count}")
        
    except Exception as e:
        print(f"❌ Error writing output CSV: {e}")


async def main():
    """Main function"""
    # Define file paths
    input_csv = "samples/input_people.csv"
    output_csv = "samples/output_locations.csv"
    
    # Check if input file exists, if not show instructions
    if not Path(input_csv).exists():
        print("="*60)
        print("📋 INPUT CSV FORMAT")
        print("="*60)
        print(f"\nCreate a file named '{input_csv}' with the following format:\n")
        print("name,college")
        print("Gavin Miyasato,Harvard University School of Public Health")
        print("John Doe,MIT")
        print("Jane Smith,Stanford University")
        print("\n" + "="*60 + "\n")
        print(f"❌ Input file '{input_csv}' not found!")
        print("Please create the input CSV file and try again.\n")
        return
    
    # Process the CSV
    await process_csv(input_csv, output_csv)


if __name__ == "__main__":
    asyncio.run(main())
