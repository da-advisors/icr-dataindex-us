#!/usr/bin/env python3
"""
Script to scrape detailed information from reginfo.gov for a specific ICR reference number
"""

import requests
from bs4 import BeautifulSoup
import argparse
import re
import json
import pandas as pd
from urllib.parse import urljoin, quote
import time

def parse_statute_link(onclick_attr):
    """
    Parse the JavaScript onclick attribute to extract statute information and create a govinfo.gov URL
    
    Args:
        onclick_attr (str): The JavaScript onclick attribute (e.g., "javascript:leavePage(' 13 USC 131 and 182', 2024, 'L')")
        
    Returns:
        dict: Dictionary containing the statute text and URL
    """
    if not onclick_attr:
        return None
    
    # Extract the statute text and year using regex
    match = re.search(r"leavePage\('([^']+)',\s*(\d+),\s*'L'\)", onclick_attr)
    if not match:
        return None
    
    statute_text = match.group(1).strip()
    year = match.group(2)
    
    # Parse the statute text to extract title and section
    # Common formats: "13 USC 131 and 182", "44 U.S.C. 3501-3521", etc.
    title_match = re.search(r"(\d+)\s+U\.?S\.?C\.?", statute_text)
    if not title_match:
        # If we can't parse the title, just return the text without a URL
        return {"StatuteText": statute_text}
    
    title = title_match.group(1)
    
    # Try to extract the section number
    section_match = re.search(r"(\d+)(?:\s+and\s+\d+|\s*-\s*\d+)?", statute_text[title_match.end():])
    if not section_match:
        # If we can't parse the section, just return the text without a URL
        return {"StatuteText": statute_text}
    
    section = section_match.group(1)
    
    # Construct the govinfo.gov URL
    # Format: https://www.govinfo.gov#citation?csh={"collection":"USCODE","selectOptions":[],"searchCriteria":[{"value":"uscpubyear","displayValue":"2018"},{"value":"usctitlenum","displayValue":"13"},{"value":"granuleclass","displayValue":"1"},{"value":"uscsectionnum","displayValue":"131"}]}
    citation_params = {
        "collection": "USCODE",
        "selectOptions": [],
        "searchCriteria": [
            {"value": "uscpubyear", "displayValue": year},
            {"value": "usctitlenum", "displayValue": title},
            {"value": "granuleclass", "displayValue": "1"},
            {"value": "uscsectionnum", "displayValue": section}
        ]
    }
    
    citation_json = json.dumps(citation_params)
    url = f"https://www.govinfo.gov#citation?csh={quote(citation_json)}"
    
    return {
        "StatuteText": statute_text,
        "URL": url,
        "Title": title,
        "Section": section,
        "Year": year
    }

def scrape_icr_details(ref_nbr):
    """
    Scrape detailed information for a specific ICR reference number
    
    Args:
        ref_nbr (str): ICR reference number (e.g., 202411-0607-004)
        
    Returns:
        dict: Dictionary containing the scraped information
    """
    base_url = "https://www.reginfo.gov/public/do/PRAViewICR"
    url = f"{base_url}?ref_nbr={ref_nbr}"
    
    print(f"Scraping data from {url}...")
    
    # Add headers to mimic a browser request
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }
    
    try:
        # First, get the page with default view
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        # Now get the page with "all" sections expanded
        all_url = f"{url}&show=all"
        time.sleep(1)  # Add a small delay to avoid being blocked
        response = requests.get(all_url, headers=headers)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Save the HTML for debugging
        with open(f"icr_{ref_nbr}_debug.html", "w", encoding="utf-8") as f:
            f.write(response.text)
        
        # Initialize the results dictionary
        results = {
            "ICRReferenceNumber": ref_nbr,
            "URL": url
        }
        
        # Extract the title from the page - look for the actual title in the table, not the page title
        title_elem = soup.find('label', string='Title:')
        if title_elem and title_elem.parent:
            # Get the next sibling text which contains the actual title
            title_text = title_elem.parent.get_text(strip=True).replace('Title:', '').strip()
            if title_text:
                results["Title"] = title_text
        
        # If we couldn't find the title using the label, try the h1 approach as fallback
        if "Title" not in results:
            title_elem = soup.find('h1')
            if title_elem:
                results["Title"] = title_elem.get_text(strip=True)
        
        # Extract the abstract - it's in section2 div
        abstract_section = soup.find('div', {'id': 'section2'})
        if abstract_section:
            abstract_text = abstract_section.get_text(strip=True)
            if abstract_text:
                # Remove the "Abstract:" prefix if present
                abstract_text = abstract_text.replace("Abstract:", "", 1).strip()
                results["Abstract"] = abstract_text
        
        # Extract Status and Type of Review Request directly from the table
        status_elem = soup.find('label', string='Status:')
        if status_elem and status_elem.parent:
            status_text = status_elem.parent.get_text(strip=True).replace('Status:', '').strip()
            if status_text:
                results["Status"] = status_text
        
        review_type_elem = soup.find('label', string='Type of Review Request:')
        if review_type_elem and review_type_elem.parent:
            review_type_text = review_type_elem.parent.get_text(strip=True).replace('Type of Review Request:', '').strip()
            if review_type_text:
                results["TypeOfReviewRequest"] = review_type_text
        
        # Extract Citations for New Statutory Requirements
        # This is typically found in section3 (Legal Statutes)
        citations_section = soup.find('div', {'id': 'section3'})
        if citations_section:
            # Look for the specific label for new statutory requirements
            new_statute_label = citations_section.find(string=lambda text: text and "Citations for New Statutory Requirements" in text)
            if new_statute_label:
                # Get the full text of the section containing the citations
                section_text = citations_section.get_text(strip=True)
                
                # Extract the part after "Citations for New Statutory Requirements:"
                citation_parts = section_text.split("Citations for New Statutory Requirements:")
                if len(citation_parts) > 1:
                    citation_text = citation_parts[1].strip()
                    
                    # Check if there's actual content beyond the label
                    if citation_text and not citation_text.startswith("Authorizing Statute"):
                        # Clean up the text by removing any trailing sections
                        if "Authorizing Statute" in citation_text:
                            citation_text = citation_text.split("Authorizing Statute")[0].strip()
                        
                        # Handle multiple citations
                        # Split by common separators like semicolons or multiple PL: occurrences
                        citations = []
                        
                        # Check if there are multiple PL: entries
                        if citation_text.count("PL:") > 1:
                            # Split by PL: but keep the PL: prefix
                            raw_citations = ["PL:" + part if i > 0 else part for i, part in enumerate(citation_text.split("PL:")[1:])]
                        else:
                            # Check for semicolon or other separators
                            if ";" in citation_text:
                                raw_citations = [c.strip() for c in citation_text.split(";")]
                            else:
                                raw_citations = [citation_text]
                        
                        # Process each citation
                        for raw_citation in raw_citations:
                            citation_dict = {}
                            
                            # Check for "Name of Law:" pattern
                            if "Name of Law:" in raw_citation:
                                pl_part, law_name_part = raw_citation.split("Name of Law:", 1)
                                citation_dict["NameOfLaw"] = law_name_part.strip()
                                
                                # Extract the PL part
                                pl_part = pl_part.strip()
                                if pl_part.startswith("PL:"):
                                    citation_dict["PL"] = pl_part[3:].strip()
                                else:
                                    citation_dict["PL"] = pl_part
                            else:
                                # If no "Name of Law:" pattern, just store the whole text as PL
                                if raw_citation.startswith("PL:"):
                                    citation_dict["PL"] = raw_citation[3:].strip()
                                else:
                                    citation_dict["PL"] = raw_citation
                            
                            if citation_dict:
                                citations.append(citation_dict)
                        
                        if citations:
                            # If there's only one citation, return it as a dict for backward compatibility
                            if len(citations) == 1:
                                results["CitationsForNewStatutoryRequirements"] = citations[0]
                            else:
                                results["CitationsForNewStatutoryRequirements"] = citations
                        else:
                            results["CitationsForNewStatutoryRequirements"] = "None"
                    else:
                        results["CitationsForNewStatutoryRequirements"] = "None"
                else:
                    results["CitationsForNewStatutoryRequirements"] = "None"
            else:
                # Look for PL: or Pub.L. which often indicates statutory citations
                pl_matches = citations_section.find_all(string=lambda text: text and ("PL:" in text or "Pub.L." in text))
                if pl_matches:
                    citations = []
                    
                    for pl_match in pl_matches:
                        pl_text = pl_match.strip()
                        citation_dict = {}
                        
                        # Check for "Name of Law:" pattern
                        if "Name of Law:" in pl_text:
                            pl_part, law_name_part = pl_text.split("Name of Law:", 1)
                            citation_dict["NameOfLaw"] = law_name_part.strip()
                            
                            # Extract the PL part
                            pl_part = pl_part.strip()
                            if pl_part.startswith("PL:"):
                                citation_dict["PL"] = pl_part[3:].strip()
                            else:
                                citation_dict["PL"] = pl_part
                        else:
                            # If no "Name of Law:" pattern, just store the whole text as PL
                            if pl_text.startswith("PL:"):
                                citation_dict["PL"] = pl_text[3:].strip()
                            else:
                                citation_dict["PL"] = pl_text
                        
                        if citation_dict:
                            citations.append(citation_dict)
                    
                    if citations:
                        # If there's only one citation, return it as a dict for backward compatibility
                        if len(citations) == 1:
                            results["CitationsForNewStatutoryRequirements"] = citations[0]
                        else:
                            results["CitationsForNewStatutoryRequirements"] = citations
                    else:
                        results["CitationsForNewStatutoryRequirements"] = "None"
                else:
                    results["CitationsForNewStatutoryRequirements"] = "None"
        else:
            results["CitationsForNewStatutoryRequirements"] = "None"
        
        # Extract authorizing statutes with URLs
        auth_statutes = []
        auth_label = soup.find('label', string=lambda text: text and "Authorizing Statute" in text)
        if auth_label:
            # Look for links with class 'pageSubNavTxt' that have href containing 'javascript:leavePage'
            statute_links = soup.find_all('a', {'class': 'pageSubNavTxt', 'href': lambda href: href and 'leavePage' in href})
            
            for link in statute_links:
                statute_text = link.get_text(strip=True)
                href_attr = link.get('href', '')
                
                # Extract the statute text and year using regex
                match = re.search(r"leavePage\('([^']+)',\s*(\d+),\s*'L'\)", href_attr)
                if match:
                    statute_text = match.group(1).strip()
                    year = match.group(2)
                    
                    # Parse the statute text to extract title and section
                    title_match = re.search(r"(\d+)\s+U\.?S\.?C\.?", statute_text)
                    if title_match:
                        title = title_match.group(1)
                        
                        # Try to extract the section number
                        section_match = re.search(r"(\d+)(?:\s+and\s+\d+|\s*-\s*\d+)?", statute_text[title_match.end():])
                        if section_match:
                            section = section_match.group(1)
                            
                            # Construct the govinfo.gov URL
                            citation_params = {
                                "collection": "USCODE",
                                "selectOptions": [],
                                "searchCriteria": [
                                    {"value": "uscpubyear", "displayValue": year},
                                    {"value": "usctitlenum", "displayValue": title},
                                    {"value": "granuleclass", "displayValue": "1"},
                                    {"value": "uscsectionnum", "displayValue": section}
                                ]
                            }
                            
                            citation_json = json.dumps(citation_params)
                            url = f"https://www.govinfo.gov#citation?csh={quote(citation_json)}"
                            
                            auth_statutes.append({
                                "StatuteText": statute_text,
                                "URL": url,
                                "Title": title,
                                "Section": section,
                                "Year": year
                            })
                        else:
                            auth_statutes.append({"StatuteText": statute_text})
                    else:
                        auth_statutes.append({"StatuteText": statute_text})
                else:
                    auth_statutes.append({"StatuteText": statute_text})
        
        results["AuthorizingStatutes"] = auth_statutes
        
        # Extract information collections
        collections = []
        # Look for links to information collections
        ic_links = soup.find_all('a', href=lambda href: href and 'PRAViewIC' in href)
        for link in ic_links:
            collection_title = link.get_text(strip=True)
            collection_url = urljoin(base_url, link['href'])
            
            # Find the next link which should be the form download link
            form_link = link.find_next('a', href=lambda href: href and 'DownloadDocument' in href)
            form_number = ""
            form_url = ""
            if form_link:
                form_number = form_link.get_text(strip=True)
                form_url = urljoin(base_url, form_link['href'])
            
            collections.append({
                "Title": collection_title,
                "URL": collection_url,
                "FormNumber": form_number,
                "FormURL": form_url
            })
        
        results["InformationCollections"] = collections
        
        # Extract burden information in a more structured way
        burden_info = {}
        burden_section = soup.find('div', {'id': 'section7'})
        if burden_section:
            # Extract Annual Number of Responses
            responses_label = burden_section.find(string=lambda text: text and "Annual Number of Responses" in text)
            if responses_label and responses_label.parent:
                next_elem = responses_label.parent.find_next_sibling()
                if next_elem:
                    burden_info["AnnualNumberOfResponses"] = next_elem.get_text(strip=True)
            
            # Extract Annual Time Burden
            time_burden_label = burden_section.find(string=lambda text: text and "Annual Time Burden" in text)
            if time_burden_label and time_burden_label.parent:
                next_elem = time_burden_label.parent.find_next_sibling()
                if next_elem:
                    burden_info["AnnualTimeBurden"] = next_elem.get_text(strip=True)
            
            # Extract Annual Cost Burden
            cost_burden_label = burden_section.find(string=lambda text: text and "Annual Cost Burden" in text)
            if cost_burden_label and cost_burden_label.parent:
                next_elem = cost_burden_label.parent.find_next_sibling()
                if next_elem:
                    burden_info["AnnualCostBurden"] = next_elem.get_text(strip=True)
        
        if burden_info:
            results["BurdenInformation"] = burden_info
        
        # Extract all labels and values to catch anything we might have missed
        all_labels = soup.find_all('label')
        for label in all_labels:
            label_text = label.get_text(strip=True).replace(':', '')
            if label_text and label_text not in results:
                # Skip junk labels that we don't want
                if label_text in [
                    "Display additional information by clicking on the following",
                    "All", "Brief", "Abstract/Justification", "Legal Statutes",
                    "Rulemaking", "FR Notices/Comments", "IC List", "Burden",
                    "Misc.", "Common Form Info.", "Certification",
                    "ICR Summary of Burden"
                ]:
                    continue
                
                # Try to find the value in the next sibling or element
                next_elem = label.find_next_sibling()
                if next_elem:
                    value = next_elem.get_text(strip=True)
                    if value:
                        # Skip if the value is just another label or contains junk text
                        if value in [
                            "All", "Brief", "Abstract/Justification", "Legal Statutes",
                            "Rulemaking", "FR Notices/Comments", "IC List", "Burden",
                            "Misc.", "Common Form Info.", "Certification",
                            "View Information Collection (IC) List",
                            "View Supporting Statement and Other Documents"
                        ]:
                            continue
                        
                        results[label_text] = value
        
        # Extract direct links to Federal Register notices
        fr_links = soup.find_all('a', href=lambda href: href and 'leavePage' in href and 'FR' in href)
        fr_notices = []
        for link in fr_links:
            fr_text = link.get_text(strip=True)
            fr_notices.append(fr_text)
        
        results["FederalRegisterNotices"] = fr_notices
        
        return results
    
    except Exception as e:
        print(f"Error scraping {url}: {str(e)}")
        import traceback
        traceback.print_exc()
        return {"ICRReferenceNumber": ref_nbr, "Error": str(e)}

def main():
    parser = argparse.ArgumentParser(description="Scrape detailed information from reginfo.gov for a specific ICR reference number")
    parser.add_argument("ref_nbr", help="ICR reference number (e.g., 202411-0607-004)")
    parser.add_argument("--output", "-o", help="Output file (JSON or CSV)")
    parser.add_argument("--format", "-f", choices=["json", "csv"], default="json", help="Output format (default: json)")
    
    args = parser.parse_args()
    
    # Scrape the data
    data = scrape_icr_details(args.ref_nbr)
    
    # Print the results
    print("\nScraped Data:")
    for key, value in data.items():
        if isinstance(value, dict) or isinstance(value, list):
            print(f"{key}: {json.dumps(value, indent=2)}")
        else:
            print(f"{key}: {value}")
    
    # Save the data if an output file is specified
    if args.output:
        if args.format == "json":
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            print(f"\nData saved to {args.output} in JSON format")
        else:  # CSV format
            # Convert nested structures to strings for CSV
            flat_data = {}
            for key, value in data.items():
                if isinstance(value, (dict, list)):
                    flat_data[key] = json.dumps(value)
                else:
                    flat_data[key] = value
            
            # Convert to DataFrame and save as CSV
            df = pd.DataFrame([flat_data])
            df.to_csv(args.output, index=False)
            print(f"\nData saved to {args.output} in CSV format")

if __name__ == "__main__":
    main()
