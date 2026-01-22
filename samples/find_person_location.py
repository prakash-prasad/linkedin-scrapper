#!/usr/bin/env python3
"""
Example: Find a person's location by name and college

This example shows how to search for a LinkedIn profile by name and college,
then extract their location.
"""
import asyncio
from playwright.async_api import Page
from linkedin_scraper.core.browser import BrowserManager
from linkedin_scraper.scrapers.person import PersonScraper


async def _search_profile_on_linkedin(page: Page, search_name: str, college: str) -> tuple[str, str]:
    """
    Search for a profile on LinkedIn and scrape location.
    
    Args:
        page: Playwright page object (must be logged in)
        search_name: Name to search for
        college: College/University name
        
    Returns:
        Tuple of (profile_url, location) or (None, error_msg)
    """
    try:
        # Navigate to LinkedIn search
        search_query = f"{search_name} {college}"
        search_url = f"https://www.linkedin.com/search/results/people/?keywords={search_query.replace(' ', '%20')}"
        
        await page.goto(search_url)
        await page.wait_for_selector("main", timeout=10000)
        
        # Wait for search results to load
        await asyncio.sleep(1)
        
        # Find the first profile link in search results
        profile_links = await page.locator('a[href*="/in/"]').all()
        
        if not profile_links:
            return None, "No profile found"
        
        # Get the first profile URL
        profile_url = await profile_links[0].get_attribute('href')
        
        # Clean up the URL to remove any query parameters
        if '?' in profile_url:
            profile_url = profile_url.split('?')[0]
        
        # Ensure it's a complete URL
        if not profile_url.startswith('http'):
            profile_url = f"https://www.linkedin.com{profile_url}"
        
        # Use PersonScraper to get location
        scraper = PersonScraper(page)
        location = await scraper.scrape(profile_url)
        
        return profile_url, location if location else "Location not available"
    
    except Exception as e:
        return None, f"Error: {str(e)}"


def _generate_name_combinations(full_name: str) -> list[str]:
    """
    Generate name combinations to try if the full name doesn't work.
    
    For a 3-word name (first middle last), generate alternatives by excluding middle name.
    
    Args:
        full_name: Person's full name
        
    Returns:
        List of name combinations to try in order
    """
    words = full_name.strip().split()
    
    # Start with full name
    combinations = [full_name]
    
    # If 3 words, try combinations excluding middle name
    if len(words) == 3:
        w1, w2, w3 = words
        # Try first + last (w1 w3)
        combinations.append(f"{w1} {w3}")
        # Try first + middle (w1 w2)
        combinations.append(f"{w1} {w2}")
    
    # Remove duplicates while preserving order
    seen = set()
    unique_combos = []
    for combo in combinations:
        if combo not in seen:
            seen.add(combo)
            unique_combos.append(combo)
    
    return unique_combos


async def find_person_location_on_page(page: Page, name: str, college: str) -> tuple[str, str]:
    """
    Find a person's location on LinkedIn using an existing page/session.
    
    Implements smart fallback: if full name search fails and name has 3 words,
    tries alternative combinations (first+last, first+middle).
    
    Args:
        page: Playwright page object (must be logged in)
        name: Person's full name
        college: College/University name
        
    Returns:
        Tuple of (profile_url, location) or (None, error_msg)
    """
    try:
        # Generate name combinations to try
        name_combinations = _generate_name_combinations(name)
        
        last_error = None
        
        for attempt, search_name in enumerate(name_combinations, 1):
            profile_url, result = await _search_profile_on_linkedin(page, search_name, college)
            
            if profile_url:
                # Success!
                if attempt > 1:
                    # Log if we had to use alternative name combination
                    import logging
                    logging.debug(f"Found profile using alternative name '{search_name}' (attempted {attempt}/{len(name_combinations)})")
                return profile_url, result
            else:
                # Track the error
                last_error = result
        
        # All combinations tried, none worked
        return None, f"No profile found. Tried combinations: {name_combinations}"
    
    except Exception as e:
        return None, f"Error: {str(e)}"


async def find_person_location(name: str, college: str) -> tuple[str, str]:
    """
    Find a person's location on LinkedIn by their name and college.
    
    Backward-compatible wrapper that opens its own BrowserManager.
    For batch processing, use find_person_location_on_page directly.
    
    Args:
        name: Person's full name
        college: College/University name
        
    Returns:
        Tuple of (profile_url, location)
    """
    async with BrowserManager(headless=False) as browser:
        # Load existing session
        await browser.load_session("linkedin_session.json")
        print("✓ Session loaded\n")
        
        print(f"🔍 Searching for '{name}' from '{college}'...")
        profile_url, location = await find_person_location_on_page(browser.page, name, college)
        
        if profile_url:
            print(f"✓ Found profile: {profile_url}\n")
        else:
            print(f"❌ Not found: {location}\n")
        
        return profile_url, location


async def main():
    """Main function to demonstrate finding person location"""
    # Example: search for Bill Gates
    name = "Gavin Miyasato"
    college = "Harvard University School of Public Health"
    
    try:
        profile_url, location = await find_person_location(name, college)
        
        if profile_url and location:
            print("="*60)
            print(f"Name: {name}")
            print(f"College: {college}")
            print(f"Profile URL: {profile_url}")
            print(f"Location: {location}")
            print("="*60)
        elif profile_url:
            print("="*60)
            print(f"Name: {name}")
            print(f"College: {college}")
            print(f"Profile URL: {profile_url}")
            print(f"Location: Not available")
            print("="*60)
        else:
            print("Could not find the person on LinkedIn")
    
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
