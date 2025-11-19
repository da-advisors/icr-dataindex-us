#!/usr/bin/env python3
"""
ICR Scraper
Uses the existing working scraper from praxml_parsing/scrape_single_icr.py
to extract ICR data and update our database table
"""

import os
import sys
import json
import logging
import subprocess
import psycopg2
import re
import argparse
import time
from datetime import datetime, date, timedelta
from dateutil import parser as date_parser
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def get_db_connection():
    """
    Get a connection to the PostgreSQL database using environment variables
    """
    try:
        # Get database connection parameters from environment variables
        db_host = os.environ.get('DB_HOST')
        db_name = os.environ.get('DB_NAME')
        db_user = os.environ.get('DB_USER')
        db_password = os.environ.get('DB_PASSWORD')
        db_port = os.environ.get('DB_PORT', '5432')

        if not all([db_host, db_name, db_user, db_password]):
            raise ValueError("Missing required database environment variables (DB_HOST, DB_NAME, DB_USER, DB_PASSWORD)")

        # Connect to the database
        connection = psycopg2.connect(
            host=db_host,
            database=db_name,
            user=db_user,
            password=db_password,
            port=db_port
        )

        logger.info(f"Successfully connected to database {db_name} on {db_host}")
        return connection
    except Exception as e:
        logger.error(f"Error connecting to database: {e}")
        return None

def get_xml_data_from_db(ref_nbr, connection, schema):
    """
    Get XML data for this ICR from the beta.pra_xml_icrs table
    """
    try:
        schema_prefix = f"{schema}." if schema else ""
        cursor = connection.cursor()
        
        # Query the pra_xml_icrs table for this ICR
        query = f"SELECT * FROM {schema_prefix}pra_xml_icrs WHERE icr_reference_number = %s"
        cursor.execute(query, (ref_nbr,))
        result = cursor.fetchone()
        
        if not result:
            logger.warning(f"No XML data found in database for {ref_nbr}")
            return {}
        
        # Get column names
        column_names = [desc[0] for desc in cursor.description]
        
        # Convert to dictionary
        xml_data = {}
        for i, col_name in enumerate(column_names):
            xml_data[col_name] = result[i]
        
        # Convert to format expected by the scraper
        converted_data = {
            'ICRReferenceNumber': xml_data.get('icr_reference_number'),
            'OMBControlNumber': xml_data.get('omb_control_number'),
            'AgencyCode': xml_data.get('agency_code'),
            'AgencyName': xml_data.get('agency_name'),
            'SubAgencyName': xml_data.get('subagency_name'),
            'Title': xml_data.get('title'),
            'Abstract': xml_data.get('abstract'),
            'ICRStatus': xml_data.get('status'),
            'ExpirationDate': xml_data.get('expiration_date'),
            'SubmissionDate': xml_data.get('submission_date'),
            'ConclusionDate': xml_data.get('conclusion_date'),
            'ConclusionAction': xml_data.get('conclusion_action'),
            'DetailURL': f"https://www.reginfo.gov/public/do/PRAViewICR?ref_nbr={ref_nbr}"
        }
        
        logger.info(f"Found XML data in database for {ref_nbr}")
        return converted_data
    except Exception as e:
        logger.error(f"Error getting XML data from database: {str(e)}")
        return {}

def parse_statute_link(href):
    """
    Parse the JavaScript leavePage function to extract statute information and create a govinfo.gov URL
    
    Args:
        href (str): The JavaScript href attribute (e.g., "javascript:leavePage(' 13 USC 131 ', 2024, 'L')")
        
    Returns:
        dict: Dictionary containing the statute text and URL
    """
    if not href or 'leavePage' not in href:
        return None
    
    # Extract the statute text and year using regex
    match = re.search(r"leavePage\('([^']+)'(?:,\s*(\d+))?[,)]?", href)
    if not match:
        return None
    
    statute_text = match.group(1).strip()
    year = match.group(2) if match.group(2) else "2022"  # Default to current year if not specified
    
    # Skip Federal Register Notices - handles various formats
    if re.match(r'^\d{1,3}\s+FR\s+\d{1,5}', statute_text):
        logger.info(f"Skipping Federal Register Notice: {statute_text}")
        return None
    
    # Parse the statute text to extract title and section for USC citations
    if 'USC' in statute_text:
        # Common formats: "13 USC 131", "44 U.S.C. 3501-3521", "34 USC Section 10132", etc.
        title_match = re.search(r"(\d+)\s+U\.?S\.?C\.?", statute_text)
        if not title_match:
            # If we can't parse the title, just return the text without a URL
            return {"StatuteText": statute_text, "URL": None}
        
        title = title_match.group(1)
        
        # Try to extract the section number
        section_match = re.search(r"(?:Section|Sec\.?|§)?\s*(\d+)(?:\s+and\s+\d+|\s*-\s*\d+)?", statute_text[title_match.end():])
        if not section_match:
            # If we can't parse the section, just return the text without a URL
            return {"StatuteText": statute_text, "URL": None}
        
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
        
        return {
            "StatuteText": statute_text,
            "URL": url
        }
    
    # For Public Laws, generate a URL to govinfo.gov
    if 'Pub.L' in statute_text or 'Public Law' in statute_text:
        # Common formats: "Pub.L. 107 - 279 303", "Public Law 94-142"
        pl_match = re.search(r"Pub\.?L\.?\s*(\d+)\s*-\s*(\d+)", statute_text)
        if pl_match:
            congress = pl_match.group(1)
            law_num = pl_match.group(2)
            
            # Construct a govinfo.gov URL for Public Laws
            citation_params = {
                "collection": "PLAW",
                "selectOptions": [],
                "searchCriteria": [
                    {"value": "congress", "displayValue": congress},
                    {"value": "lawnum", "displayValue": law_num},
                    {"value": "lawtype", "displayValue": "PUBLIC"},
                    {"value": "docnumber", "displayValue": law_num}
                ]
            }
            
            citation_json = json.dumps(citation_params)
            url = f"https://www.govinfo.gov#citation?csh={quote(citation_json)}"
            
            return {
                "StatuteText": statute_text,
                "URL": url
            }
    
    # If we couldn't generate a URL, just return the text
    return {"StatuteText": statute_text, "URL": None}

def extract_authorizing_statutes(soup):
    """
    Extract all authorizing statutes from the ICR page
    Returns a list of dictionaries with statute information
    Excludes Federal Register Notices
    """
    statutes = []
    law_names = []
    
    try:
        logger.info("Starting authorizing statute extraction...")
        
        # First, try to find all law names on the page
        # Look for patterns like "Name of Law: Law Name" or similar structures
        law_name_patterns = [
            re.compile(r"Name of Law:\s*([^\n]+)"),
            re.compile(r"Authorizing Law:\s*([^\n]+)"),
            re.compile(r"Statute:\s*([^\n]+)")
        ]
        
        page_text = soup.get_text()
        
        # Try each pattern to find law names
        for pattern in law_name_patterns:
            matches = pattern.findall(page_text)
            for match in matches:
                name = match.strip()
                if name and name not in law_names:
                    law_names.append(name)
                    logger.info(f"Found law name: {name}")
        
        # If we didn't find any law names with the patterns, look for them near statute citations
        if not law_names:
            # Find sections that might contain law names
            sections = soup.find_all(['p', 'div', 'tr'])
            for section in sections:
                text = section.get_text(strip=True)
                if "USC" in text or "U.S.C." in text or "Public Law" in text:
                    # Look for potential law names in this section
                    for line in section.get_text().split('\n'):
                        line = line.strip()
                        if line and "USC" not in line and "U.S.C." not in line and "Public Law" not in line and len(line) > 10:
                            if line not in law_names:
                                law_names.append(line)
                                logger.info(f"Found potential law name from context: {line}")
        
        # Find all links that might be authorizing statutes
        # These typically appear as javascript:leavePage links with USC or Public Law citations
        statute_links = soup.find_all('a', href=lambda href: href and 'leavePage' in href)
        logger.info(f"Found {len(statute_links)} potential statute links on the page")
        
        # Extract the text from each link
        for i, link in enumerate(statute_links):
            statute_text = link.get_text(strip=True)
            href = link.get('href', '')
            logger.info(f"Processing link: text='{statute_text}', href='{href}'")
            
            # Parse the statute link to get text and URL
            statute_info = parse_statute_link(href)
            
            # Skip if it's a Federal Register Notice or couldn't be parsed
            if not statute_info:
                continue
            
            # Skip if we already have this statute (avoid duplicates)
            if any(s['StatuteText'] == statute_info['StatuteText'] for s in statutes):
                continue
            
            # Try to associate this statute with a law name
            # If we have multiple law names, try to match them with statutes in order
            if i < len(law_names):
                statute_info['LawName'] = law_names[i]
            elif law_names:
                # If we have fewer law names than statutes, use the last law name for remaining statutes
                statute_info['LawName'] = law_names[-1]
            else:
                statute_info['LawName'] = None
            
            # Add to our list of statutes
            statutes.append(statute_info)
            logger.info(f"Found authorizing statute: {statute_info['StatuteText']}, URL: {statute_info['URL']}, Law Name: {statute_info['LawName']}")
    
    except Exception as e:
        logger.warning(f"Error extracting authorizing statutes: {str(e)}")
    
    return {
        'statutes': statutes,
        'law_names': law_names
    }

def scrape_icr_details(ref_nbr):
    """
    Use the existing working scraper to get ICR details and combine with XML data
    """
    try:
        # Path to the scraper (now in the same directory)
        scraper_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scrape_single_icr.py")
        
        # Create a temp directory if it doesn't exist
        temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp")
        if not os.path.exists(temp_dir):
            os.makedirs(temp_dir)
            logger.info(f"Created temp directory at {temp_dir}")
        
        # Create a temporary file to store the output in the temp directory
        temp_file = os.path.join(temp_dir, f"temp_icr_{ref_nbr}.json")
        
        # Construct the command to run the existing scraper
        command = [
            sys.executable,
            scraper_path,
            ref_nbr,
            "--output", temp_file,
            "--format", "json"
        ]
        
        # Log the command
        logger.info(f"Running command: {' '.join(command)}")
        
        # Run the command
        result = subprocess.run(command, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"Error running scraper: {result.stderr}")
            return {"Error": result.stderr, "ICRReferenceNumber": ref_nbr}
        
        # Read the output file
        with open(temp_file, 'r') as f:
            data = json.load(f)
        
        # Clean up the temporary file
        try:
            os.remove(temp_file)
            logger.debug(f"Removed temporary file: {temp_file}")
        except Exception as e:
            logger.warning(f"Could not remove temporary file {temp_file}: {e}")
        
        # Get the law name directly from the ICR page
        try:
            # Import statements moved to top of file
            
            # Get the ICR page with all details
            url = f"https://www.reginfo.gov/public/do/PRAViewICR?ref_nbr={ref_nbr}&show=all"
            response = requests.get(url)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract data from the main table at the top of the page
            # This table contains most of the key metadata
            main_table = soup.find('table', {'class': 'pageSubNavTable'})
            if main_table:
                # Process each row in the table
                rows = main_table.find_all('tr')
                for row in rows:
                    cells = row.find_all('td')
                    for cell in cells:
                        # Look for labels within the cell
                        labels = cell.find_all('label')
                        for label in labels:
                            label_text = label.get_text(strip=True).rstrip(':')
                            # Find the value which is typically the text after the label
                            cell_text = cell.get_text(strip=True)
                            if f"{label_text}:" in cell_text:
                                value = cell_text.split(f"{label_text}:")[1].strip()
                                data[label_text] = value
                                logger.info(f"Found {label_text}: {value}")
            
            # Directly extract specific fields we need based on the HTML structure
            # These mappings match what you see on the page
            field_mappings = {
                'Agency/Subagency': 'Agency/Subagency',
                'Type of Review Request': 'Type of Review Request',
                'Date Received in OIRA': 'Date Received',
                'Conclusion Date': 'Conclusion Date',
                'OIRA Conclusion Action': 'Conclusion Action',
                'Request Type': 'Request Type',
                'Type of Information Collection': 'Type of Information Collection',
                'Citations for New Statutory Requirements': 'Citations for New Statutory Requirements'
            }
            
            # Extract all text from the page
            page_text = soup.get_text()
            
            # Look for each field in the page text
            for original_field, mapped_field in field_mappings.items():
                pattern = f"{original_field}:\s*([^\n]+)"  # Match field name followed by value until newline
                match = re.search(pattern, page_text)
                if match:
                    value = match.group(1).strip()
                    data[mapped_field] = value
                    logger.info(f"Found {mapped_field}: {value}")
            
            # Extract authorizing statutes using the dedicated function
            authorizing_statutes_data = extract_authorizing_statutes(soup)
            
            # Add the extracted data to our results
            if authorizing_statutes_data['law_names']:
                data['AuthorizingStatuteLawNames'] = authorizing_statutes_data['law_names']
                logger.info(f"Found law names: {authorizing_statutes_data['law_names']}")
            
            # Store the statutes list
            data['AuthorizingStatutes'] = authorizing_statutes_data['statutes']
            if data['AuthorizingStatutes']:
                logger.info(f"Found {len(data['AuthorizingStatutes'])} authorizing statutes")
                
                # Log each statute with its associated law name
                for statute in data['AuthorizingStatutes']:
                    logger.info(f"Statute: {statute['StatuteText']}, Law Name: {statute.get('LawName', 'Unknown')}")

        except Exception as e:
            logger.warning(f"Error extracting additional data from HTML: {str(e)}")
        
        # Return the scraped data (we'll combine with XML data in the scrape_and_update_icr function)
        return data
    except Exception as e:
        logger.error(f"Error running scraper: {str(e)}")
        return {"Error": str(e), "ICRReferenceNumber": ref_nbr}

def update_scrape_status(ref_nbr, connection, schema, status="success", error_message=None):
    """
    Update the scrape status in the pra_xml_icrs table
    """
    try:
        schema_prefix = f"{schema}." if schema else ""
        cursor = connection.cursor()
        
        # Update the scrape status
        query = f"""
            UPDATE {schema_prefix}pra_xml_icrs
            SET 
                last_scraped = NOW(),
                needs_scraping = FALSE,
                scrape_status = %s
            WHERE icr_reference_number = %s
        """
        cursor.execute(query, (status, ref_nbr))
        connection.commit()
        
        # Log the error message separately
        if error_message:
            logger.warning(f"Error for {ref_nbr}: {error_message}")
            
        logger.info(f"Updated scrape status for {ref_nbr} to {status}")
        return True
    except Exception as e:
        connection.rollback()
        logger.error(f"Error updating scrape status: {str(e)}")
        return False

def scrape_and_update_icr(ref_nbr, connection, schema):
    """
    Scrape ICR details and update the database
    """
    try:
        # Get XML data from the database
        xml_data = get_xml_data_from_db(ref_nbr, connection, schema)
        
        # Scrape ICR details from reginfo.gov
        icr_data = scrape_icr_details(ref_nbr)
        
        # Combine XML data with scraped data
        if xml_data:
            # Update with XML data if available
            for key, value in xml_data.items():
                if key not in icr_data or not icr_data[key]:
                    icr_data[key] = value
        
        # Update the database with the combined data
        success = update_database(icr_data, connection, schema)
        
        # Update the scrape status in the pra_xml_icrs table
        if success:
            update_scrape_status(ref_nbr, connection, schema, "success")
        else:
            update_scrape_status(ref_nbr, connection, schema, "error", "Failed to update database")
        
        return icr_data
    except Exception as e:
        # Update the scrape status with the error
        update_scrape_status(ref_nbr, connection, schema, "error", str(e))
        logger.error(f"Error scraping and updating ICR: {str(e)}")
        return {"Error": str(e), "ICRReferenceNumber": ref_nbr}


def parse_date(date_str):
    """
    Parse a date string into a Python date object
    Handles various formats like MM/DD/YYYY, YYYY-MM-DD, etc.
    """
    if not date_str or date_str == 'None':
        return None
        
    try:
        # Try to parse the date string
        parsed_date = date_parser.parse(date_str)
        return parsed_date.date()  # Return just the date part, not time
    except Exception as e:
        logger.warning(f"Error parsing date '{date_str}': {str(e)}")
        return None

def update_database(icr_data, connection, schema):
    """
    Update the database with scraped ICR data
    """
    try:
        schema_prefix = f"{schema}." if schema else ""
        cursor = connection.cursor()
        
        # Check if the ICR exists in the database
        query = f"SELECT id FROM {schema_prefix}pra_icr_details WHERE icr_reference_number = %s"
        cursor.execute(query, (icr_data['ICRReferenceNumber'],))
        result = cursor.fetchone()
        
        # Extract fields from the scraped data
        title = icr_data.get('Title', '')
        agency = icr_data.get('Agency/Subagency', '')
        abstract = icr_data.get('Abstract', '')
        icr_type = icr_data.get('Type of Information Collection', '')
        affected_public = icr_data.get('Affected Public', '')
        obligation_to_respond = icr_data.get('Obligation to Respond', '')
        annual_responses = icr_data.get('Annual Number of Responses', '')
        annual_burden_hours = icr_data.get('Annual Time Burden (Hours)', '')
        annual_cost_burden = icr_data.get('Annual Cost Burden ($)', '')
        authorizing_statute_text = icr_data.get('AuthorizingStatuteText', '')
        authorizing_statute_url = icr_data.get('AuthorizingStatuteURL', '')
        authorizing_statute_law_name = icr_data.get('AuthorizingStatuteLawName', '')
        
        # If we have multiple statutes, store them as a JSON array
        if 'AuthorizingStatutes' in icr_data and icr_data['AuthorizingStatutes']:
            # Convert the list of statute dictionaries to JSON
            statute_texts = [s['StatuteText'] for s in icr_data['AuthorizingStatutes'] if 'StatuteText' in s]
            if statute_texts:
                authorizing_statute_text = json.dumps(statute_texts)
                logger.info(f"Storing {len(statute_texts)} statutes as JSON: {authorizing_statute_text}")
            
            # Store the URLs as JSON as well
            urls = [s['URL'] for s in icr_data['AuthorizingStatutes'] if 'URL' in s]
            if urls:
                authorizing_statute_url = json.dumps(urls)
                logger.info(f"Storing statute URLs as JSON: {authorizing_statute_url}")
            
            # Store the law names as JSON
            if 'AuthorizingStatuteLawNames' in icr_data and icr_data['AuthorizingStatuteLawNames']:
                # Use the law names array directly
                authorizing_statute_law_name = json.dumps(icr_data['AuthorizingStatuteLawNames'])
                logger.info(f"Storing law names as JSON: {authorizing_statute_law_name}")
            elif any('LawName' in s and s['LawName'] for s in icr_data['AuthorizingStatutes']):
                # Extract law names from each statute
                law_names = [s['LawName'] for s in icr_data['AuthorizingStatutes'] if 'LawName' in s and s['LawName']]
                if law_names:
                    authorizing_statute_law_name = json.dumps(law_names)
                    logger.info(f"Storing law names from statutes as JSON: {authorizing_statute_law_name}")
        
        # If we didn't get the text from the complex structure, use the simple field
        if not authorizing_statute_text:
            authorizing_statute_text = icr_data.get('Authorizing Statute(s)')
            logger.info(f"Using simple authorizing statute: {authorizing_statute_text}")
        
            
        # Extract Federal Register Notices
        frn_60_day = None
        frn_60_day_url = None
        frn_30_day = None
        frn_30_day_url = None
        
        if 'FederalRegisterNotices' in icr_data and isinstance(icr_data['FederalRegisterNotices'], list):
            # Try to identify 60-day and 30-day notices
            # For now, we'll assume the first notice is the 60-day and the second is the 30-day
            # This is a simplification and may need refinement based on actual data patterns
            if len(icr_data['FederalRegisterNotices']) >= 1:
                frn_60_day = icr_data['FederalRegisterNotices'][0]
                frn_60_day_url = f"https://www.federalregister.gov/citation/{frn_60_day.replace(' ', '-')}"
                
            if len(icr_data['FederalRegisterNotices']) >= 2:
                frn_30_day = icr_data['FederalRegisterNotices'][1]
                frn_30_day_url = f"https://www.federalregister.gov/citation/{frn_30_day.replace(' ', '-')}"
        
        # Prepare data for database - use all available fields from both XML and web-scraped data
        db_data = {
            'icr_reference_number': icr_data.get('ICRReferenceNumber'),
            'icr_title': icr_data.get('Title'),
            'abstract': icr_data.get('Abstract'),
            'status': icr_data.get('Status') or icr_data.get('ICRStatus'),
            'review_request_type': icr_data.get('TypeOfReviewRequest'),
            'icr_url': icr_data.get('URL') or icr_data.get('DetailURL'),
            'last_scraped': datetime.now(),
            'scrape_status': 'success' if 'Error' not in icr_data else 'error',
            'error_message': icr_data.get('Error'),
            
            # Basic ICR fields
            'date_received': parse_date(icr_data.get('Date Received') or icr_data.get('Date Received in OIRA') or icr_data.get('SubmissionDate')),
            'concluded_date': parse_date(icr_data.get('Conclusion Date') or icr_data.get('ConclusionDate')),
            'conclusion_action': icr_data.get('Conclusion Action') or icr_data.get('OIRA Conclusion Action') or icr_data.get('ConclusionAction'),
            'current_expiration_date': parse_date(icr_data.get('Current Expiration Date') or icr_data.get('ExpirationDate')),
            'omb_control_number': icr_data.get('OMB Control Number') or icr_data.get('OMBControlNumber'),
            'omb_control_number_url': f"https://www.reginfo.gov/public/do/PRAOMBHistory?ombControlNumber={icr_data.get('OMB Control Number') or icr_data.get('OMBControlNumber')}" if icr_data.get('OMB Control Number') or icr_data.get('OMBControlNumber') else None,
            'agency_subagency': icr_data.get('Agency/Subagency') or f"{icr_data.get('AgencyName', '')} / {icr_data.get('SubAgencyName', '')}".strip(' /'),
            'request_type': icr_data.get('Type of Information Collection') or icr_data.get('Request Type'),
            'citations_for_new_statutory_requirements': icr_data.get('Citations for New Statutory Requirements'),
            
            # Authorizing Statute fields
            'authorizing_statute_text': authorizing_statute_text,
            'authorizing_statute_url': authorizing_statute_url,
            'authorizing_statute_law_name': authorizing_statute_law_name,
            
            # Federal Register Notice fields
            'frn_60_day': frn_60_day,
            'frn_60_day_url': frn_60_day_url,
            'frn_30_day': frn_30_day,
            'frn_30_day_url': frn_30_day_url,
            
            # Other ICR fields
            'previous_icr_reference_number': icr_data.get('Previous ICR Reference No'),
            'public_comments_received': icr_data.get('Did the Agency receive public comments on this ICR?')
        }
        
        # Log the authorizing statute data that will be stored
        logger.info(f"Authorizing statute data to be stored:")
        logger.info(f"  Text: {db_data.get('authorizing_statute_text')}")
        logger.info(f"  URL: {db_data.get('authorizing_statute_url')}")
        logger.info(f"  Law Name: {db_data.get('authorizing_statute_law_name')}")
        
        # Remove None values to avoid SQL errors
        db_data = {k: v for k, v in db_data.items() if v is not None}
        
        if result:
            # ICR exists, update it
            icr_id = result[0]
            set_clause = ", ".join([f"{k} = %s" for k in db_data.keys()])
            values = list(db_data.values())
            values.append(icr_id)  # For the WHERE clause
            
            query = f"UPDATE {schema_prefix}pra_icr_details SET {set_clause} WHERE id = %s"
            cursor.execute(query, values)
            logger.info(f"Updated database record for {icr_data['ICRReferenceNumber']}")
        else:
            # ICR doesn't exist, insert it
            columns = ", ".join(db_data.keys())
            placeholders = ", ".join(["%s" for _ in db_data.keys()])
            values = list(db_data.values())
            
            query = f"INSERT INTO {schema_prefix}pra_icr_details ({columns}) VALUES ({placeholders})"
            cursor.execute(query, values)
            logger.info(f"Inserted new database record for {icr_data['ICRReferenceNumber']}")
        
        connection.commit()
        return True
    except Exception as e:
        connection.rollback()
        logger.error(f"Error updating database: {str(e)}")
        return False

def get_all_icrs_from_db(connection, schema, prioritize=True, needs_scraping_only=True, new_only=False):
    """
    Get all ICR reference numbers from the pra_xml_icrs table
    Optionally prioritize by needs_scraping and file_type
    If new_only is True, only return ICRs that are new or have been updated since the last scrape
    """
    try:
        schema_prefix = f"{schema}." if schema else ""
        cursor = connection.cursor()
        
        # If new_only is True, we only want ICRs that need scraping
        if new_only:
            needs_scraping_only = True
        
        if prioritize:
            # Query to get ICRs prioritized by needs_scraping and file_type
            needs_scraping_clause = "AND needs_scraping = TRUE" if needs_scraping_only else ""
            query = f"""
                WITH prioritized AS (
                    SELECT 
                        icr_reference_number,
                        CASE 
                            WHEN needs_scraping = TRUE THEN 0  -- Highest priority: needs scraping
                            WHEN file_type LIKE '%concluded%' THEN 1
                            WHEN file_type LIKE '%pending%' THEN 2
                            WHEN file_type LIKE '%expiration%' THEN 3
                            WHEN file_type LIKE '%inventory%' THEN 4
                            ELSE 5
                        END as priority
                    FROM {schema_prefix}pra_xml_icrs
                    WHERE 1=1 {needs_scraping_clause}
                )
                SELECT DISTINCT icr_reference_number, priority
                FROM prioritized
                ORDER BY priority, icr_reference_number
            """
        else:
            # Simple query to get all distinct ICR reference numbers
            needs_scraping_clause = "WHERE needs_scraping = TRUE" if needs_scraping_only else ""
            query = f"SELECT DISTINCT icr_reference_number FROM {schema_prefix}pra_xml_icrs {needs_scraping_clause} ORDER BY icr_reference_number"
        
        # Log the query being executed
        logger.info(f"Executing query: {query}")
        
        cursor.execute(query)
        results = cursor.fetchall()
        
        # Extract ICR reference numbers from results
        icr_ref_numbers = [row[0] for row in results]
        
        # Log the number of ICRs found
        logger.info(f"Found {len(icr_ref_numbers)} ICRs in the database that need scraping" if needs_scraping_only else f"Found {len(icr_ref_numbers)} ICRs in the database")
        
        # Log the list of ICRs found (first 10 for brevity)
        if icr_ref_numbers:
            logger.info(f"First 10 ICRs: {', '.join(icr_ref_numbers[:10])}")
            if len(icr_ref_numbers) > 10:
                logger.info(f"... and {len(icr_ref_numbers) - 10} more")
        return icr_ref_numbers
    except Exception as e:
        logger.error(f"Error getting ICRs from database: {str(e)}")
        return []

def cleanup_temp_files():
    """
    Clean up any temporary files in the temp directory
    """
    temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp")
    if os.path.exists(temp_dir):
        try:
            # Count files before cleanup
            files_before = len([f for f in os.listdir(temp_dir) if os.path.isfile(os.path.join(temp_dir, f))])
            
            # Remove all files in the temp directory
            for filename in os.listdir(temp_dir):
                file_path = os.path.join(temp_dir, filename)
                if os.path.isfile(file_path):
                    os.remove(file_path)
            
            # Count files after cleanup
            files_after = len([f for f in os.listdir(temp_dir) if os.path.isfile(os.path.join(temp_dir, f))])
            
            if files_before > 0:
                logger.info(f"Cleaned up temp directory: removed {files_before - files_after} files")
        except Exception as e:
            logger.warning(f"Error cleaning up temp directory: {e}")

def main():
    """
    Main function
    """
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='ICR Scraper')
    parser.add_argument('--limit', type=int, help='Limit the number of ICRs to process')
    parser.add_argument('--resume', type=str, help='Resume from a specific ICR reference number')
    parser.add_argument('--specific', type=str, help='Process a specific ICR reference number')
    parser.add_argument('--no-prioritize', action='store_true', help='Do not prioritize ICRs by file type')
    parser.add_argument('--new-only', action='store_true', help='Only process ICRs that are new or have been updated since the last scrape')
    parser.add_argument('--dry-run', action='store_true', help='Do not update the database, just print what would be done')
    args = parser.parse_args()
    
    # Check for required environment variables
    if not all([os.environ.get('DB_HOST'), os.environ.get('DB_NAME'),
                os.environ.get('DB_USER'), os.environ.get('DB_PASSWORD')]):
        logger.error("Missing required database environment variables")
        logger.error("Please set: DB_HOST, DB_NAME, DB_USER, DB_PASSWORD, DB_PORT (optional)")
        logger.error("You can also set DB_SCHEMA (defaults to 'beta')")
        sys.exit(1)

    schema = os.environ.get('DB_SCHEMA', 'beta')
    
    # Connect to the database
    connection = get_db_connection()
    
    if connection:
        start_time = time.time()
        
        # If a specific ICR is requested, just process that one
        if args.specific:
            # Process a specific ICR
            icr_ref_numbers = [args.specific]
            logger.info(f"Processing specific ICR: {args.specific}")
        else:
            # Get all ICRs from the database, prioritized by file_type
            icr_ref_numbers = get_all_icrs_from_db(connection, schema, prioritize=not args.no_prioritize, new_only=args.new_only)
        
        # If resume is specified, start from that ICR
        if args.resume and args.resume in icr_ref_numbers:
            resume_index = icr_ref_numbers.index(args.resume)
            logger.info(f"Resuming from ICR {args.resume} (skipping {resume_index} ICRs)")
            icr_ref_numbers = icr_ref_numbers[resume_index:]
        
        # If limit is specified, only process that many ICRs
        if args.limit and args.limit > 0:
            logger.info(f"Limiting to {args.limit} ICRs")
            icr_ref_numbers = icr_ref_numbers[:args.limit]
        
        # Process each ICR
        total_icrs = len(icr_ref_numbers)
        successful = 0
        failed = 0
        
        logger.info(f"Starting to process {total_icrs} ICRs")
        
        for i, ref_nbr in enumerate(icr_ref_numbers):
            icr_start_time = time.time()
            logger.info(f"\n{'='*80}\nProcessing ICR {i+1}/{total_icrs}: {ref_nbr}\n{'='*80}")
            
            try:
                # Scrape and update the ICR
                if not args.dry_run:
                    icr_data = scrape_and_update_icr(ref_nbr, connection, schema)
                    if icr_data and 'Error' not in icr_data:
                        successful += 1
                    else:
                        failed += 1
                else:
                    logger.info(f"DRY RUN: Would process ICR {ref_nbr}")
                    successful += 1
                
                # Calculate and log timing information
                icr_duration = time.time() - icr_start_time
                elapsed_total = time.time() - start_time
                icrs_per_second = (i + 1) / elapsed_total if elapsed_total > 0 else 0
                estimated_remaining = (total_icrs - (i + 1)) / icrs_per_second if icrs_per_second > 0 else 0
                
                logger.info(f"ICR {i+1}/{total_icrs} completed in {icr_duration:.2f} seconds")
                logger.info(f"Progress: {(i+1)/total_icrs*100:.1f}% complete, {successful} successful, {failed} failed")
                logger.info(f"Elapsed time: {timedelta(seconds=int(elapsed_total))}, Estimated remaining: {timedelta(seconds=int(estimated_remaining))}")
                
            except Exception as e:
                logger.error(f"Error processing ICR {ref_nbr}: {str(e)}")
                failed += 1
                continue  # Continue with the next ICR
        
        # Log final statistics
        total_duration = time.time() - start_time
        logger.info(f"\n{'='*80}\nScraping completed\n{'='*80}")
        logger.info(f"Total ICRs processed: {total_icrs}")
        logger.info(f"Successful: {successful}")
        logger.info(f"Failed: {failed}")
        logger.info(f"Total duration: {timedelta(seconds=int(total_duration))}")
        if total_icrs > 0:
            logger.info(f"Average time per ICR: {total_duration/total_icrs:.2f} seconds")
        
        # Clean up any temporary files
        cleanup_temp_files()
        
        # Close the database connection
        connection.close()
    else:
        logger.error("Failed to connect to database")

if __name__ == "__main__":
    main()
