# Essential Data Archive - PostgreSQL Development Setup

This repository contains a Docker-based development environment for the Essential Data Archive project, providing a local PostgreSQL database for webapp development.

## Setup

### Prerequisites

- Docker and Docker Compose

### Getting Started

1. Start the Docker containers:

```bash
docker-compose up -d
```

2. Access pgAdmin at http://localhost:5050 (login with email: admin@example.com, password: admin)
3. Connect your webapp to the database using the connection string in the `.env` file

## Project Structure

```
.
├── init-scripts/           # SQL scripts for database initialization
│   └── 01-schema.sql       # Schema definition script
├── docker-compose.yaml     # Docker Compose configuration
└── .env                    # Environment variables and connection strings
```

## Database Schema

The database is initialized with the following schemas:

1. **monitoring**: For tracking resource status
   - resources: Information about monitored URLs
   - resource_status: Historical status checks
   - collections: Groups of related resources

2. **pra**: For Paperwork Reduction Act data
   - information_collection_requests: ICR data from XML files

3. **sorn**: For System of Records Notice data
   - system_of_records_notices: SORN data from Federal Register

## Connecting Your Webapp

Use the connection string in the `.env` file to connect your webapp to the database:

```
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/essentialdata
```

## Connecting to EC2 PostgreSQL

To connect to your EC2 PostgreSQL instance instead of the local one:

1. Update the `.env` file with your EC2 PostgreSQL connection details
2. Update your webapp's database connection configuration

## Notes

- This is a development setup and not intended for production use
- Data is persisted in Docker volumes for local development
- The schema is automatically created when the container starts
# icr-dataindex-us
