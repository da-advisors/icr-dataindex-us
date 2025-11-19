"""
PRA XML Parser - Local Test Version

This script simulates the Lambda function but runs locally and uses the
existing db_config.ini file instead of environment variables.
"""

import os
import sys
import xml.etree.ElementTree as ET
import requests
import json
import logging
import hashlib
import csv
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import DictCursor, execute_values
import configparser
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Custom JSON encoder to handle datetime objects
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# URLs for the XML files
XML_URLS = {
    'inventory': 'https://www.reginfo.gov/public/do/PRAXML?type=inventory',
    'pending': 'https://www.reginfo.gov/public/do/PRAXML?type=pending',
    'concluded': 'https://www.reginfo.gov/public/do/PRAXML?type=concluded',
    'expiration': 'https://www.reginfo.gov/public/do/PRAXML?type=expiration'
}

# File naming convention
FILE_NAMES = {
    'inventory': 'CurrentInventoryReport.xml',
    'pending': 'PaperworkPending.xml',
    'concluded': 'PaperworkReviewsConcluded.xml',
    'expiration': 'PRAExpirationReport.xml'
}

# Update frequency - used to determine if we should re-download
UPDATE_FREQUENCY = {
    'inventory': 'daily',
    'pending': 'daily',
    'concluded': 'daily',
    'expiration': 'monthly'
}

# Base URL for ICR details
ICR_DETAIL_BASE_URL = "https://www.reginfo.gov/public/do/PRAViewICR?ref_nbr="

def ensure_directory_exists(directory):
    """Ensure that the specified directory exists."""
    if not os.path.exists(directory):
        os.makedirs(directory)
        return f"Created directory: {directory}"
    return f"Directory already exists: {directory}"

def get_file_hash(file_path):
    """Calculate MD5 hash of a file to detect changes."""
    if not os.path.exists(file_path):
        return None
    
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def should_download_file(file_type, file_path, force_refresh=False):
    """
    Determine if we should download a file based on its type and last modification time.
    """
    if force_refresh:
        return True
        
    if not os.path.exists(file_path):
        return True
    
    if UPDATE_FREQUENCY[file_type] == 'daily':
        return True
    
    # For monthly files, check if it's been more than 24 hours since last check
    last_modified = os.path.getmtime(file_path)
    last_modified_time = datetime.fromtimestamp(last_modified)
    current_time = datetime.now()
    
    # If it's been more than 24 hours, we should check for updates
    return (current_time - last_modified_time) > timedelta(hours=24)

def get_element_text(element, tag_name, default=''):
    """Helper function to safely get element text."""
    el = element.find(f'./{tag_name}')
    return el.text if el is not None and el.text is not None else default

def get_db_connection(config_file=None):
    """Get a connection to the PostgreSQL database using environment variables or config file."""
    try:
        # First try environment variables
        db_host = os.environ.get('DB_HOST')
        db_name = os.environ.get('DB_NAME')
        db_user = os.environ.get('DB_USER')
        db_password = os.environ.get('DB_PASSWORD')
        db_port = os.environ.get('DB_PORT', '5432')

        # If environment variables not found, try config file
        if not all([db_host, db_name, db_user, db_password]):
            if config_file and os.path.exists(config_file):
                config = configparser.ConfigParser()
                config.read(config_file)
                db_host = config['postgresql']['host']
                db_name = config['postgresql']['database']
                db_user = config['postgresql']['user']
                db_password = config['postgresql']['password']
                db_port = config['postgresql']['port']
            else:
                raise ValueError("Database credentials not found in environment variables or config file")

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
        raise

def setup_database(connection):
    """Set up the database schema if it doesn't exist."""
    try:
        # Get schema from config if available
        schema = None
        try:
            config = configparser.ConfigParser()
            config.read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "db_config.ini"))
            if 'postgresql' in config and 'schema' in config['postgresql']:
                schema = config['postgresql']['schema']
                logger.info(f"Using database schema: {schema}")
        except Exception as e:
            logger.warning(f"Could not read schema from config: {e}")
        
        with connection.cursor() as cursor:
            # Create schema if specified and doesn't exist
            if schema:
                cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
                connection.commit()
                logger.info(f"Created schema if it didn't exist: {schema}")
            
            # Create the icrs table if it doesn't exist
            schema_prefix = f"{schema}." if schema else ""
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {schema_prefix}pra_xml_icrs (
                    id SERIAL PRIMARY KEY,
                    icr_reference_number VARCHAR(255) NOT NULL,
                    omb_control_number VARCHAR(255),
                    agency_code VARCHAR(50),
                    title TEXT,
                    abstract TEXT,
                    icr_status VARCHAR(50),
                    file_type VARCHAR(50),
                    run_date VARCHAR(50),
                    expiration_date VARCHAR(50),
                    submission_date VARCHAR(50),
                    conclusion_date VARCHAR(50),
                    conclusion_action VARCHAR(255),
                    detail_url TEXT,
                    burden_response_count VARCHAR(50),
                    burden_hours VARCHAR(50),
                    burden_cost VARCHAR(50),
                    data_hash VARCHAR(255),
                    last_updated TIMESTAMP,
                    needs_scraping BOOLEAN DEFAULT TRUE,
                    last_scraped TIMESTAMP,
                    scrape_status VARCHAR(50),
                    UNIQUE(icr_reference_number, file_type)
                );
                
                CREATE INDEX IF NOT EXISTS idx_icr_reference_number ON {schema_prefix}pra_xml_icrs(icr_reference_number);
                CREATE INDEX IF NOT EXISTS idx_omb_control_number ON {schema_prefix}pra_xml_icrs(omb_control_number);
                CREATE INDEX IF NOT EXISTS idx_file_type ON {schema_prefix}pra_xml_icrs(file_type);
                CREATE INDEX IF NOT EXISTS idx_needs_scraping ON {schema_prefix}pra_xml_icrs(needs_scraping);
            """)
            
            # Create the icr_changes table to track changes
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {schema_prefix}pra_xml_icr_changes (
                    id SERIAL PRIMARY KEY,
                    icr_reference_number VARCHAR(255) NOT NULL,
                    file_type VARCHAR(50),
                    change_type VARCHAR(50),
                    change_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    old_data JSONB,
                    new_data JSONB
                );
                
                CREATE INDEX IF NOT EXISTS idx_changes_icr_reference_number ON {schema_prefix}pra_xml_icr_changes(icr_reference_number);
                CREATE INDEX IF NOT EXISTS idx_changes_change_date ON {schema_prefix}pra_xml_icr_changes(change_date);
            """)
            
            connection.commit()
            logger.info("Database schema set up successfully")
            return "Database schema set up successfully"
    
    except Exception as e:
        connection.rollback()
        logger.error(f"Error setting up database schema: {e}")
        raise Exception(f"Error setting up database schema: {e}")

def download_xml_files(data_dir, force_refresh=False):
    """
    Download XML files from reginfo.gov and store them locally.
    """
    # Ensure data directory exists
    ensure_directory_exists(data_dir)
    
    # Get current date for file naming
    timestamp = datetime.now().strftime("%Y%m%d")
    timestamped_dir = os.path.join(data_dir, timestamp)
    ensure_directory_exists(timestamped_dir)
    
    # Also maintain a "latest" directory that always has the most recent files
    latest_dir = os.path.join(data_dir, "latest")
    ensure_directory_exists(latest_dir)
    
    saved_files = {}
    download_metadata = {}
    
    for file_type, url in XML_URLS.items():
        latest_file_path = os.path.join(latest_dir, FILE_NAMES[file_type])
        timestamped_file_path = os.path.join(timestamped_dir, FILE_NAMES[file_type])
        
        # Check if we should download this file
        if should_download_file(file_type, latest_file_path, force_refresh):
            try:
                logger.info(f"Downloading {file_type} XML from {url}")
                response = requests.get(url)
                response.raise_for_status()  # Raise an exception for HTTP errors
                
                # For monthly files, check if the content has changed before saving
                if UPDATE_FREQUENCY[file_type] == 'monthly' and os.path.exists(latest_file_path) and not force_refresh:
                    old_hash = get_file_hash(latest_file_path)
                    
                    # Calculate hash of new content
                    new_hash = hashlib.md5(response.content).hexdigest()
                    
                    if old_hash == new_hash:
                        logger.info(f"Monthly {file_type} file hasn't changed, using existing file")
                        saved_files[file_type] = latest_file_path
                        download_metadata[file_type] = {
                            "status": "unchanged",
                            "path": latest_file_path,
                            "size": os.path.getsize(latest_file_path),
                        }
                        continue
                
                # Save to both timestamped and latest directories
                with open(timestamped_file_path, 'wb') as f:
                    f.write(response.content)
                
                with open(latest_file_path, 'wb') as f:
                    f.write(response.content)
                
                logger.info(f"Saved {file_type} XML to {timestamped_file_path} and {latest_file_path}")
                saved_files[file_type] = latest_file_path
                download_metadata[file_type] = {
                    "status": "downloaded",
                    "path": latest_file_path,
                    "size": os.path.getsize(latest_file_path),
                    "timestamp": timestamp,
                }
                
            except requests.exceptions.RequestException as e:
                logger.error(f"Error downloading {file_type} XML: {e}")
                
                # If download fails but we have a latest file, use that
                if os.path.exists(latest_file_path):
                    logger.info(f"Using existing {file_type} file: {latest_file_path}")
                    saved_files[file_type] = latest_file_path
                    download_metadata[file_type] = {
                        "status": "fallback",
                        "path": latest_file_path,
                        "size": os.path.getsize(latest_file_path),
                        "error": str(e),
                    }
        else:
            logger.info(f"Using existing {file_type} file: {latest_file_path}")
            saved_files[file_type] = latest_file_path
            download_metadata[file_type] = {
                "status": "existing",
                "path": latest_file_path,
                "size": os.path.getsize(latest_file_path),
            }
    
    logger.info(f"Download summary: {json.dumps(download_metadata)}")
    return saved_files

def parse_xml_data(xml_files):
    """
    Parse XML files and extract ICR data.
    """
    parsed_data = {}
    
    for file_type, file_path in xml_files.items():
        try:
            logger.info(f"Parsing XML file: {file_path}")
            
            # Parse XML
            tree = ET.parse(file_path)
            root = tree.getroot()
            
            # Extract run date from the XML
            run_date = root.get('RUNDATE', 'Unknown')
            
            icr_list = []
            
            # Extract data from each ICR
            for icr in root.findall('.//InformationCollectionRequest'):
                abstract_text = get_element_text(icr, 'Abstract')
                icr_data = {
                    'omb_control_number': get_element_text(icr, 'OMBControlNumber'),
                    'icr_reference_number': get_element_text(icr, 'ICRReferenceNumber'),
                    'agency_code': get_element_text(icr, 'AgencyCode'),
                    'title': get_element_text(icr, 'Title'),
                    'abstract': abstract_text,
                    'icr_status': get_element_text(icr, 'ICRStatus'),
                    'file_type': file_type,
                    'run_date': run_date,
                    'last_updated': datetime.now().isoformat(),
                }
                
                # Log abstract extraction for debugging
                if abstract_text:
                    logger.info(f"Extracted abstract for ICR {icr_data['icr_reference_number']} (length: {len(abstract_text)}): {abstract_text[:100]}...")
                else:
                    logger.info(f"No abstract found for ICR {icr_data['icr_reference_number']}")
                
                # Add expiration date if available
                expiration_date = icr.find('.//ExpirationDate')
                if expiration_date is not None:
                    icr_data['expiration_date'] = expiration_date.text
                
                # Add submission date if available (for pending ICRs)
                submission_date = icr.find('.//SubmissionDate/Date')
                if submission_date is not None:
                    icr_data['submission_date'] = submission_date.text
                
                # Add conclusion date if available (for concluded ICRs)
                conclusion_date = icr.find('.//ConclusionDate/Date')
                if conclusion_date is not None:
                    icr_data['conclusion_date'] = conclusion_date.text
                
                # Generate the URL for the ICR detail page
                if icr_data['icr_reference_number']:
                    icr_data['detail_url'] = f"{ICR_DETAIL_BASE_URL}{icr_data['icr_reference_number']}"
                
                # Extract burden information if available
                burden_response = icr.find('.//BurdenResponse/TotalQuantity')
                if burden_response is not None:
                    icr_data['burden_response_count'] = burden_response.text
                
                burden_hour = icr.find('.//BurdenHour/TotalQuantity')
                if burden_hour is not None:
                    icr_data['burden_hours'] = burden_hour.text
                
                burden_cost = icr.find('.//BurdenCost/TotalAmount')
                if burden_cost is not None:
                    icr_data['burden_cost'] = burden_cost.text
                
                # Add any additional fields specific to certain file types
                if file_type == 'concluded':
                    conclusion_action = icr.find('.//ConclusionAction')
                    if conclusion_action is not None:
                        icr_data['conclusion_action'] = conclusion_action.text
                
                # Create a copy of the data without timestamp fields for hashing
                hash_data = icr_data.copy()
                # Remove fields that change on every run but don't represent actual data changes
                if 'last_updated' in hash_data:
                    del hash_data['last_updated']
                if 'run_date' in hash_data:
                    del hash_data['run_date']
                
                # Add a hash of the ICR data for change detection
                icr_data['data_hash'] = hashlib.md5(json.dumps(hash_data, sort_keys=True, cls=DateTimeEncoder).encode()).hexdigest()
                
                icr_list.append(icr_data)
            
            logger.info(f"Extracted {len(icr_list)} ICRs from {file_type}")
            parsed_data[file_type] = {
                'run_date': run_date,
                'icr_count': len(icr_list),
                'icrs': icr_list
            }
            
        except Exception as e:
            logger.error(f"Error parsing XML file {file_path}: {e}")
            parsed_data[file_type] = {
                'run_date': 'Error',
                'icr_count': 0,
                'icrs': []
            }
    
    return parsed_data

def update_database(parsed_data, db_connection):
    """
    Update the database with parsed ICR data and return new/changed ICRs.
    Uses batch processing for better performance.
    """
    # Get schema from config if available
    schema = None
    try:
        config = configparser.ConfigParser()
        config.read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "db_config.ini"))
        if 'postgresql' in config and 'schema' in config['postgresql']:
            schema = config['postgresql']['schema']
    except Exception:
        pass
    
    schema_prefix = f"{schema}." if schema else ""
    
    # Get existing ICRs from the database
    existing_icrs = {}
    
    try:
        with db_connection.cursor(cursor_factory=DictCursor) as cursor:
            # Check if the table exists and has the expected schema
            try:
                cursor.execute(f"""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name = 'pra_xml_icrs'
                    {f"AND table_schema = '{schema}'" if schema else ''}
                """)
                columns = [row[0] for row in cursor.fetchall()]
                logger.info(f"Table pra_xml_icrs exists with columns: {columns}")
            except Exception as e:
                logger.error(f"Error checking table schema: {e}")
            
            # Get existing ICRs
            cursor.execute(f"SELECT * FROM {schema_prefix}pra_xml_icrs")
            rows = cursor.fetchall()
            
            for row in rows:
                row_dict = dict(row)
                key = (row_dict['icr_reference_number'], row_dict['file_type'])
                existing_icrs[key] = row_dict
            
            logger.info(f"Retrieved {len(existing_icrs)} existing ICRs from database")
    
    except Exception as e:
        logger.error(f"Error retrieving existing ICRs: {e}")
        raise Exception(f"Database error: {e}")
    
    # Update the database with new ICR data
    new_icrs = []
    changed_icrs = []
    unchanged_icrs = []
    
    # Calculate total ICRs for progress reporting
    total_icrs = sum(len(data['icrs']) for data in parsed_data.values())
    logger.info(f"Processing {total_icrs} ICRs in batches")
    
    try:
        with db_connection.cursor() as cursor:
            # Process each file type
            for file_type, data in parsed_data.items():
                logger.info(f"Processing {len(data['icrs'])} ICRs from {file_type} file")
                
                # Prepare batches for bulk processing
                batch_size = 100
                icr_batches = [data['icrs'][i:i + batch_size] for i in range(0, len(data['icrs']), batch_size)]
                
                # Process each batch
                for batch_index, batch in enumerate(icr_batches):
                    batch_new = []
                    batch_changed = []
                    batch_unchanged = []
                    
                    # First pass: categorize ICRs
                    for icr in batch:
                        key = (icr['icr_reference_number'], icr['file_type'])
                        
                        if key not in existing_icrs:
                            # New ICR
                            batch_new.append(icr)
                            new_icrs.append(icr)
                        else:
                            # Existing ICR - check if it has changed
                            existing_icr = existing_icrs[key]
                            
                            # Debug the first few ICRs to see why they're being marked as changed
                            if len(changed_icrs) < 3 and len(unchanged_icrs) == 0:
                                # Create hash data for comparison
                                new_hash_data = icr.copy()
                                old_hash_data = dict(existing_icr).copy()
                                
                                # Remove fields that shouldn't affect the hash
                                for field in ['last_updated', 'run_date', 'data_hash']:
                                    if field in new_hash_data:
                                        del new_hash_data[field]
                                    if field in old_hash_data:
                                        del old_hash_data[field]
                                
                                # Log the differences
                                logger.info(f"Debug ICR {icr['icr_reference_number']} hash comparison:")
                                logger.info(f"New hash: {icr['data_hash']}")
                                logger.info(f"Old hash: {existing_icr['data_hash']}")
                                
                                # Find differences in the data
                                for key in set(new_hash_data.keys()) | set(old_hash_data.keys()):
                                    if key not in new_hash_data:
                                        logger.info(f"Key missing in new data: {key}")
                                    elif key not in old_hash_data:
                                        logger.info(f"Key missing in old data: {key}")
                                    elif new_hash_data[key] != old_hash_data[key]:
                                        logger.info(f"Different value for {key}: Old={old_hash_data[key]}, New={new_hash_data[key]}")
                            
                            if icr['data_hash'] != existing_icr['data_hash']:
                                # ICR has changed
                                batch_changed.append(icr)
                                changed_icrs.append(icr)
                            else:
                                # ICR is unchanged
                                batch_unchanged.append(icr)
                                unchanged_icrs.append(icr)
                    
                    # Process new ICRs in bulk if possible
                    if batch_new:
                        # Prepare for bulk insert
                        if len(batch_new) > 0:
                            # Get column names from first ICR
                            columns = list(batch_new[0].keys())
                            # Remove 'id' column if present to let PostgreSQL auto-generate it
                            if 'id' in columns:
                                columns.remove('id')
                            # Log columns to ensure abstract is included
                            logger.info(f"Columns for database insert: {columns}")
                            columns_str = ', '.join(columns)
                            placeholders = ', '.join(['%s'] * len(columns))
                            
                            # Prepare values for bulk insert
                            values = []
                            for icr in batch_new:
                                # Only include values for columns in our filtered list
                                row_values = [icr.get(col) for col in columns]
                                # Log abstract value for the first few ICRs
                                if len(values) < 3 and 'abstract' in icr and icr['abstract']:
                                    abstract_idx = columns.index('abstract') if 'abstract' in columns else -1
                                    if abstract_idx >= 0:
                                        logger.info(f"Abstract for ICR {icr['icr_reference_number']} being inserted: {row_values[abstract_idx][:100]}...")
                                values.append(row_values)
                            
                            # Get the current maximum ID from the table and add a buffer to ensure we're beyond any manually inserted IDs
                            cursor.execute(f"SELECT MAX(id) FROM {schema_prefix}pra_xml_icrs")
                            max_id = cursor.fetchone()[0] or 0
                            # Add a buffer to ensure we're beyond any manually inserted IDs
                            next_id = max_id + 100
                            
                            # Set the sequence to start from our new ID
                            cursor.execute(f"SELECT setval('{schema_prefix}pra_xml_icrs_id_seq', {next_id}, true)")
                            
                            # Bulk insert
                            query = f"INSERT INTO {schema_prefix}pra_xml_icrs ({columns_str}) VALUES ({placeholders})"
                            psycopg2.extras.execute_batch(cursor, query, values)
                            
                            # Record changes individually (can't easily bulk insert with different JSON)
                            change_query = f"INSERT INTO {schema_prefix}pra_xml_icr_changes (icr_reference_number, file_type, change_type, new_data) VALUES (%s, %s, %s, %s)"
                            change_values = [(icr['icr_reference_number'], icr['file_type'], 'new', json.dumps(icr, cls=DateTimeEncoder)) for icr in batch_new]
                            psycopg2.extras.execute_batch(cursor, change_query, change_values)
                    
                    # Process changed ICRs individually (can't easily bulk update)
                    for icr in batch_changed:
                        existing_icr = existing_icrs[(icr['icr_reference_number'], icr['file_type'])]
                        
                        # Log abstract field for debugging
                        if 'abstract' in icr:
                            old_abstract = existing_icr.get('abstract', '')
                            new_abstract = icr.get('abstract', '')
                            if old_abstract != new_abstract:
                                logger.info(f"Abstract for ICR {icr['icr_reference_number']} is being updated:")
                                logger.info(f"  Old abstract ({len(old_abstract) if old_abstract else 0} chars): {old_abstract[:100]}..." if old_abstract else "  Old abstract: None")
                                logger.info(f"  New abstract ({len(new_abstract) if new_abstract else 0} chars): {new_abstract[:100]}..." if new_abstract else "  New abstract: None")
                        
                        # Update the database
                        set_clause = ', '.join([f"{k} = %s" for k in icr.keys()])
                        query = f"UPDATE {schema_prefix}pra_xml_icrs SET {set_clause} WHERE icr_reference_number = %s AND file_type = %s"
                        cursor.execute(query, list(icr.values()) + [icr['icr_reference_number'], icr['file_type']])
                        
                        # Record the change
                        query = f"INSERT INTO {schema_prefix}pra_xml_icr_changes (icr_reference_number, file_type, change_type, old_data, new_data) VALUES (%s, %s, %s, %s, %s)"
                        cursor.execute(query, (icr['icr_reference_number'], icr['file_type'], 'changed', json.dumps(dict(existing_icr), cls=DateTimeEncoder), json.dumps(icr, cls=DateTimeEncoder)))
                    
                    # Process unchanged ICRs in bulk
                    if batch_unchanged:
                        update_query = f"UPDATE {schema_prefix}pra_xml_icrs SET last_updated = %s WHERE icr_reference_number = %s AND file_type = %s"
                        update_values = [(icr['last_updated'], icr['icr_reference_number'], icr['file_type']) for icr in batch_unchanged]
                        psycopg2.extras.execute_batch(cursor, update_query, update_values)
                    
                    # Commit after each batch
                    db_connection.commit()
                    
                    # Report progress
                    processed = (batch_index + 1) * batch_size
                    if processed > len(data['icrs']):
                        processed = len(data['icrs'])
                    logger.info(f"Processed {processed}/{len(data['icrs'])} ICRs from {file_type} file")
                
                logger.info(f"Completed processing {file_type} file")
            
            logger.info(f"Database updated: {len(new_icrs)} new ICRs, {len(changed_icrs)} changed ICRs, {len(unchanged_icrs)} unchanged ICRs")
            
            db_connection.commit()
            logger.info(f"Database updated: {len(new_icrs)} new ICRs, {len(changed_icrs)} changed ICRs, {len(unchanged_icrs)} unchanged ICRs")
    
    except Exception as e:
        logger.error(f"Error updating database: {e}")
        db_connection.rollback()
        raise Exception(f"Database update error: {e}")
    
    return {
        "new_icrs": new_icrs,
        "changed_icrs": changed_icrs,
        "unchanged_icrs": unchanged_icrs,
    }

def generate_scraper_input(update_results, data_dir, connection=None):
    """
    Generate a JSON file with ICRs that need to be scraped.
    Also updates the needs_scraping flag in the database for all new and changed ICRs.
    """
    # Combine new and changed ICRs
    new_icrs = update_results["new_icrs"]
    changed_icrs = update_results["changed_icrs"]
    icrs_to_scrape = new_icrs + changed_icrs
    
    # Create a simplified version for the scraper
    scraper_input = []
    icr_ref_numbers = set()  # Track unique ICR reference numbers
    
    for icr in icrs_to_scrape:
        scraper_input.append({
            'ICRReferenceNumber': icr['icr_reference_number'],
            'OMBControlNumber': icr['omb_control_number'],
            'Title': icr['title'],
            'DetailURL': icr['detail_url'] if 'detail_url' in icr else '',
            'FileType': icr['file_type'],
            'ExpirationDate': icr['expiration_date'] if 'expiration_date' in icr else '',
            'SubmissionDate': icr['submission_date'] if 'submission_date' in icr else '',
            'ConclusionDate': icr['conclusion_date'] if 'conclusion_date' in icr else '',
        })
        icr_ref_numbers.add(icr['icr_reference_number'])
    
    # Update the needs_scraping flag in the database
    if connection and icr_ref_numbers:
        try:
            # Get schema from config if available
            schema = None
            try:
                config = configparser.ConfigParser()
                config.read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "db_config.ini"))
                if 'postgresql' in config and 'schema' in config['postgresql']:
                    schema = config['postgresql']['schema']
            except Exception:
                pass
            
            schema_prefix = f"{schema}." if schema else ""
            cursor = connection.cursor()
            
            # Update the needs_scraping flag for each ICR
            updated_count = 0
            for icr in icr_ref_numbers:
                query = f"""
                    UPDATE {schema_prefix}pra_xml_icrs
                    SET needs_scraping = TRUE
                    WHERE icr_reference_number = %s
                """
                cursor.execute(query, (icr,))
                updated_count += cursor.rowcount
            
            connection.commit()
            logger.info(f"Updated needs_scraping flag for {updated_count} ICRs in the database")
            
            # Check if any ICRs were not updated
            if updated_count < len(icr_ref_numbers):
                logger.warning(f"{len(icr_ref_numbers) - updated_count} ICRs were not found in the database")
        
        except Exception as e:
            connection.rollback()
            logger.error(f"Error updating needs_scraping flag: {str(e)}")
    
    # Save to file
    timestamp = datetime.now().strftime("%Y%m%d")
    file_path = os.path.join(data_dir, f"{timestamp}_icr_scraper_input.json")
    
    try:
        with open(file_path, 'w') as f:
            json.dump(scraper_input, f, indent=2)
        
        logger.info(f"Saved scraper input with {len(scraper_input)} ICRs to {file_path}")
        logger.info(f"Found {len(icr_ref_numbers)} unique ICR reference numbers")
        
        return {
            "file_path": file_path,
            "icrs_to_scrape": len(scraper_input),
            "unique_icrs": len(icr_ref_numbers),
            "timestamp": timestamp,
        }
    
    except Exception as e:
        logger.error(f"Error saving scraper input: {e}")
        raise Exception(f"Error saving scraper input: {e}")

def main(force_refresh=False):
    """
    Main function to run the PRA XML parser locally.
    """
    try:
        # Set up paths
        base_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(base_dir, "data")
        db_config_file = os.path.join(base_dir, "db_config.ini")

        logger.info(f"Starting PRA XML parser with force_refresh={force_refresh}")
        logger.info(f"Using data directory: {data_dir}")

        # Get database connection (will try environment variables first, then config file)
        connection = get_db_connection(db_config_file if os.path.exists(db_config_file) else None)
        
        # Set up database schema
        setup_database(connection)
        
        # Download XML files
        xml_files = download_xml_files(data_dir, force_refresh)
        
        # Parse XML data
        parsed_data = parse_xml_data(xml_files)
        
        # Update database
        update_results = update_database(parsed_data, connection)
        
        # Generate scraper input and update needs_scraping flags in the database
        scraper_info = generate_scraper_input(update_results, data_dir, connection)
        
        # Close database connection
        connection.close()
        
        # Return results
        result = {
            'message': 'PRA XML parsing completed successfully',
            'new_icrs': len(update_results['new_icrs']),
            'changed_icrs': len(update_results['changed_icrs']),
            'unchanged_icrs': len(update_results['unchanged_icrs']),
            'scraper_input': scraper_info,
        }
        
        logger.info(f"Results: {json.dumps(result, indent=2)}")
        return result
    
    except Exception as e:
        logger.error(f"Error in main function: {e}")
        return {
            'message': 'Error processing PRA XML files',
            'error': str(e)
        }

if __name__ == "__main__":
    # Parse command line arguments
    import argparse
    parser = argparse.ArgumentParser(description='PRA XML Parser - Local Test')
    parser.add_argument('--force-refresh', action='store_true', help='Force refresh of all files')
    args = parser.parse_args()
    
    # Run the main function
    main(force_refresh=args.force_refresh)
