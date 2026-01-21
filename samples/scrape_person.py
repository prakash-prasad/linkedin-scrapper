#!/usr/bin/env python3
"""
Example: Scrape a LinkedIn profile location

This example shows how to use the PersonScraper to get a LinkedIn profile's location.
"""
import asyncio
from linkedin_scraper.scrapers.person import PersonScraper
from linkedin_scraper.core.browser import BrowserManager


async def main():
    """Scrape a person's location from their profile"""
    profile_url = "https://www.linkedin.com/in/williamhgates/"
    
    # Initialize and start browser using context manager
    async with BrowserManager(headless=False) as browser:
        # Load existing session (must be created first - see README for setup)
        await browser.load_session("linkedin_session.json")
        print("✓ Session loaded")
        
        # Initialize scraper with the browser page
        scraper = PersonScraper(browser.page)
        
        # Scrape the location
        print(f"🚀 Scraping: {profile_url}")
        location = await scraper.scrape(profile_url)
        
        # Display result
        print("\n" + "="*60)
        print(f"Location: {location}")
        print("="*60)
    
    print("\n✓ Done!")


if __name__ == "__main__":
    asyncio.run(main())
