import sys
import json
import os
import logging
import traceback
import csv
import io
import time
import ssl
import requests
from simple_salesforce import Salesforce
from dotenv import load_dotenv
import math

# Setup Logging
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Global connection variable
sf_conn = None

def initialize_salesforce(retry_count=3):
    """Initialize Salesforce connection with SSL handling."""
    global sf_conn
    
    for attempt in range(retry_count):
        try:
            logging.info(f"=== Initializing Salesforce Connection (Attempt {attempt + 1}) ===")
            
            # Load environment variables
            load_dotenv()
            logging.info("Environment variables loaded")
            
            # Get credentials
            username = os.getenv('SALESFORCE_USERNAME')
            password = os.getenv('SALESFORCE_PASSWORD')
            security_token = os.getenv('SALESFORCE_SECURITY_TOKEN')
            instance_url = os.getenv('SALESFORCE_INSTANCE_URL')
            
            # Log what we have (safely)
            logging.info(f"Username: {username[:10] + '***' if username else 'NOT SET'}")
            logging.info(f"Password: {'SET (' + str(len(password)) + ' chars)' if password else 'NOT SET'}")
            logging.info(f"Security Token: {'SET (' + str(len(security_token)) + ' chars)' if security_token else 'NOT SET'}")
            logging.info(f"Instance URL: {instance_url if instance_url else 'NOT SET'}")
            
            # Check for missing credentials
            missing_creds = []
            if not username:
                missing_creds.append("SALESFORCE_USERNAME")
            if not password:
                missing_creds.append("SALESFORCE_PASSWORD")
            if not security_token:
                missing_creds.append("SALESFORCE_SECURITY_TOKEN")
            
            if missing_creds:
                error_msg = f"Missing required Salesforce credentials: {', '.join(missing_creds)}"
                logging.error(error_msg)
                return False, error_msg
            
            # Create a custom session with SSL handling
            logging.info("Creating custom session for SSL handling...")
            session = requests.Session()
            
            # Try multiple SSL approaches
            ssl_approaches = [
                {"name": "Default SSL", "verify": True},
                {"name": "Disable SSL verification", "verify": False},
                {"name": "Custom SSL context", "verify": True, "custom_ssl": True}
            ]
            
            for ssl_config in ssl_approaches:
                try:
                    logging.info(f"Trying SSL approach: {ssl_config['name']}")
                    
                    # Configure session SSL
                    session.verify = ssl_config['verify']
                    
                    if ssl_config.get('custom_ssl'):
                        # Create custom SSL context
                        ssl_context = ssl.create_default_context()
                        ssl_context.check_hostname = False
                        ssl_context.verify_mode = ssl.CERT_NONE
                        session.verify = False
                    
                    # Try to connect with this SSL configuration
                    connection_params = {
                        'username': username,
                        'password': password,
                        'security_token': security_token,
                        'session': session
                    }
                    
                    if instance_url:
                        connection_params['instance_url'] = instance_url
                    
                    logging.info(f"Attempting connection with {ssl_config['name']}...")
                    sf_conn = Salesforce(**connection_params)
                    logging.info("Salesforce object created successfully")
                    
                    # Test the connection with a simple query
                    logging.info("Testing connection with simple query...")
                    test_result = sf_conn.query("SELECT COUNT() FROM Account LIMIT 1")
                    account_count = test_result['totalSize']
                    
                    logging.info(f"✓ Salesforce connection successful with {ssl_config['name']}! Account count: {account_count}")
                    return True, f"Connected successfully using {ssl_config['name']}. Account count: {account_count}"
                    
                except Exception as ssl_error:
                    logging.warning(f"SSL approach '{ssl_config['name']}' failed: {str(ssl_error)}")
                    continue
            
            # If all SSL approaches failed
            raise Exception("All SSL connection approaches failed")
            
        except Exception as e:
            error_msg = f"Salesforce connection attempt {attempt + 1} failed: {str(e)}"
            logging.error(error_msg)
            logging.error(f"Exception type: {type(e).__name__}")
            
            # Log the full traceback for debugging
            traceback.print_exc(file=sys.stderr)
            
            if attempt < retry_count - 1:
                logging.info(f"Retrying in 2 seconds...")
                time.sleep(2)
            else:
                logging.error("All connection attempts failed")
                return False, error_msg
    
    return False, "All connection attempts failed"

def ensure_connection():
    """Ensure we have a valid Salesforce connection."""
    global sf_conn
    if sf_conn is None:
        success, message = initialize_salesforce()
        return success
    
    # Test if existing connection is still valid
    try:
        sf_conn.query("SELECT COUNT() FROM Account LIMIT 1")
        return True
    except Exception as e:
        logging.warning(f"Existing connection invalid: {str(e)}, reconnecting...")
        sf_conn = None
        success, message = initialize_salesforce()
        return success

def csv_to_records(csv_data):
    """Convert CSV string to list of dictionaries."""
    try:
        csv_file = io.StringIO(csv_data.strip())
        reader = csv.DictReader(csv_file)
        records = list(reader)
        logging.info(f"Parsed {len(records)} records from CSV")
        return records
    except Exception as e:
        raise Exception(f"CSV parsing error: {str(e)}")

def execute_bulk_insert_simple(object_name, csv_data):
    """Execute bulk insert using simple-salesforce bulk API."""
    try:
        if not ensure_connection():
            raise Exception("Cannot establish Salesforce connection")
            
        records = csv_to_records(csv_data)
        if not records:
            return {"error": "No records found in CSV data"}
        
        logging.info(f"Starting bulk insert of {len(records)} records to {object_name}")
        
        # Get the bulk object handler
        bulk_object = getattr(sf_conn.bulk, object_name)
        
        # Execute bulk insert
        results = bulk_object.insert(records)
        
        # Count successes and errors
        success_count = 0
        error_count = 0
        errors = []
        
        for result in results:
            if result.get('success'):
                success_count += 1
            else:
                error_count += 1
                if len(errors) < 5:  # Keep first 5 errors
                    errors.append(result.get('error', 'Unknown error'))
        
        return {
            "operation": "insert",
            "object_name": object_name,
            "total_records": len(records),
            "success_count": success_count,
            "error_count": error_count,
            "errors": errors,
            "message": f"Bulk insert completed. Success: {success_count}, Errors: {error_count}"
        }
        
    except Exception as e:
        raise Exception(f"Bulk insert failed: {str(e)}")

def execute_bulk_update_simple(object_name, csv_data):
    """Execute bulk update using simple-salesforce bulk API."""
    try:
        if not ensure_connection():
            raise Exception("Cannot establish Salesforce connection")
            
        records = csv_to_records(csv_data)
        if not records:
            return {"error": "No records found in CSV data"}
        
        # Validate that Id field exists
        if not all('Id' in record for record in records):
            return {"error": "All records must have an 'Id' field for update"}
        
        logging.info(f"Starting bulk update of {len(records)} records in {object_name}")
        
        # Get the bulk object handler
        bulk_object = getattr(sf_conn.bulk, object_name)
        
        # Execute bulk update
        results = bulk_object.update(records)
        
        # Count successes and errors
        success_count = 0
        error_count = 0
        errors = []
        
        for result in results:
            if result.get('success'):
                success_count += 1
            else:
                error_count += 1
                if len(errors) < 5:
                    errors.append(result.get('error', 'Unknown error'))
        
        return {
            "operation": "update",
            "object_name": object_name,
            "total_records": len(records),
            "success_count": success_count,
            "error_count": error_count,
            "errors": errors,
            "message": f"Bulk update completed. Success: {success_count}, Errors: {error_count}"
        }
        
    except Exception as e:
        raise Exception(f"Bulk update failed: {str(e)}")

def execute_bulk_upsert_simple(object_name, csv_data, external_id_field):
    """Execute bulk upsert using simple-salesforce bulk API."""
    try:
        if not ensure_connection():
            raise Exception("Cannot establish Salesforce connection")
            
        records = csv_to_records(csv_data)
        if not records:
            return {"error": "No records found in CSV data"}
        
        # Validate that external ID field exists
        if not all(external_id_field in record for record in records):
            return {"error": f"All records must have the '{external_id_field}' field for upsert"}
        
        logging.info(f"Starting bulk upsert of {len(records)} records in {object_name}")
        
        # Get the bulk object handler
        bulk_object = getattr(sf_conn.bulk, object_name)
        
        # Execute bulk upsert
        results = bulk_object.upsert(records, external_id_field)
        
        # Count successes and errors
        success_count = 0
        error_count = 0
        errors = []
        
        for result in results:
            if result.get('success'):
                success_count += 1
            else:
                error_count += 1
                if len(errors) < 5:
                    errors.append(result.get('error', 'Unknown error'))
        
        return {
            "operation": "upsert",
            "object_name": object_name,
            "external_id_field": external_id_field,
            "total_records": len(records),
            "success_count": success_count,
            "error_count": error_count,
            "errors": errors,
            "message": f"Bulk upsert completed. Success: {success_count}, Errors: {error_count}"
        }
        
    except Exception as e:
        raise Exception(f"Bulk upsert failed: {str(e)}")

def describe_object(object_name):
    """Get object metadata."""
    try:
        if not ensure_connection():
            raise Exception("Cannot establish Salesforce connection")
            
        describe_result = getattr(sf_conn, object_name).describe()
        
        # Extract key information
        fields_info = []
        for field in describe_result['fields']:
            fields_info.append({
                "name": field['name'],
                "type": field['type'],
                "label": field['label'],
                "required": not field['nillable'] and not field['defaultedOnCreate'],
                "updateable": field['updateable'],
                "createable": field['createable']
            })
        
        return {
            "object_name": object_name,
            "label": describe_result['label'],
            "total_fields": len(fields_info),
            "fields": fields_info,
            "createable": describe_result['createable'],
            "updateable": describe_result['updateable'],
            "deletable": describe_result['deletable']
        }
    except Exception as e:
        raise Exception(f"Describe object failed: {str(e)}")

def do_list_tools(params):
    """Lists available tools including working bulk operations."""
    return {
        "tools": [
            {
                "name": "salesforce_connection_test",
                "description": "Test the Salesforce connection and show detailed status",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            },
            {
                "name": "salesforce_query",
                "description": "Run a SOQL query (up to 2000 records)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "SOQL query"},
                        "limit": {"type": "integer", "description": "Record limit (max 2000)", "default": 200}
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "salesforce_bulk_insert_simple",
                "description": "Insert records using simple bulk method (works with simple-salesforce)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "object_name": {"type": "string", "description": "Object to insert into"},
                        "csv_data": {"type": "string", "description": "CSV formatted data with headers"}
                    },
                    "required": ["object_name", "csv_data"]
                }
            },
            {
                "name": "salesforce_bulk_update_simple",
                "description": "Update records using simple bulk method",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "object_name": {"type": "string", "description": "Object to update"},
                        "csv_data": {"type": "string", "description": "CSV formatted data with Id column"}
                    },
                    "required": ["object_name", "csv_data"]
                }
            },
            {
                "name": "salesforce_bulk_upsert_simple",
                "description": "Upsert records using simple bulk method",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "object_name": {"type": "string", "description": "Object to upsert into"},
                        "csv_data": {"type": "string", "description": "CSV formatted data"},
                        "external_id_field": {"type": "string", "description": "External ID field for upsert"}
                    },
                    "required": ["object_name", "csv_data", "external_id_field"]
                }
            },
            {
                "name": "salesforce_count_records",
                "description": "Count total records matching a query",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "object_name": {"type": "string", "description": "Object to count"},
                        "where_clause": {"type": "string", "description": "WHERE clause (optional)"}
                    },
                    "required": ["object_name"]
                }
            },
            {
                "name": "salesforce_describe_object",
                "description": "Get metadata about a Salesforce object",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "object_name": {"type": "string", "description": "Object to describe"}
                    },
                    "required": ["object_name"]
                }
            }
        ]
    }

def test_connection():
    """Test Salesforce connection and return detailed status."""
    try:
        logging.info("=== Connection Test Requested ===")
        
        # Try to initialize connection
        success, message = initialize_salesforce()
        
        if success:
            # Get additional info
            try:
                user_info = sf_conn.query("SELECT Id, Name, Username, Email FROM User WHERE Id = UserInfo.getUserId()")
                org_info = sf_conn.query("SELECT Name, OrganizationType, InstanceName FROM Organization")
                
                user_data = user_info['records'][0] if user_info['records'] else {}
                org_data = org_info['records'][0] if org_info['records'] else {}
                
                return {
                    "status": "SUCCESS",
                    "message": message,
                    "user_info": {
                        "name": user_data.get('Name', 'Unknown'),
                        "username": user_data.get('Username', 'Unknown'),
                        "email": user_data.get('Email', 'Unknown')
                    },
                    "org_info": {
                        "name": org_data.get('Name', 'Unknown'),
                        "type": org_data.get('OrganizationType', 'Unknown'),
                        "instance": org_data.get('InstanceName', 'Unknown')
                    }
                }
            except Exception as e:
                return {
                    "status": "PARTIAL_SUCCESS",
                    "message": f"Connected but couldn't get detailed info: {str(e)}"
                }
        else:
            return {
                "status": "FAILED",
                "message": message,
                "troubleshooting": [
                    "1. SSL Certificate issue detected - trying multiple SSL approaches",
                    "2. Check if you're behind a corporate firewall or proxy",
                    "3. Verify your Salesforce credentials are correct",
                    "4. Check if your IP is whitelisted in Salesforce",
                    "5. Ensure your security token is current (reset if needed)",
                    "6. Try connecting from a different network if possible"
                ]
            }
            
    except Exception as e:
        logging.error(f"Connection test failed: {str(e)}")
        traceback.print_exc(file=sys.stderr)
        return {
            "status": "ERROR",
            "message": f"Connection test error: {str(e)}"
        }

def do_call_tool(params):
    """Handle tool execution with detailed debugging."""
    tool_name = params.get('name')
    arguments = params.get('arguments', {})
    
    logging.info(f"=== Calling tool: {tool_name} ===")
    
    try:
        if tool_name == "salesforce_connection_test":
            result = test_connection()
            return {
                "content": [{
                    "type": "text",
                    "text": f"Salesforce Connection Test Results:\n\n" +
                           f"Status: {result['status']}\n" +
                           f"Message: {result['message']}\n\n" +
                           (f"User Info:\n" +
                            f"  Name: {result.get('user_info', {}).get('name', 'N/A')}\n" +
                            f"  Username: {result.get('user_info', {}).get('username', 'N/A')}\n" +
                            f"  Email: {result.get('user_info', {}).get('email', 'N/A')}\n\n" +
                            f"Organization Info:\n" +
                            f"  Name: {result.get('org_info', {}).get('name', 'N/A')}\n" +
                            f"  Type: {result.get('org_info', {}).get('type', 'N/A')}\n" +
                            f"  Instance: {result.get('org_info', {}).get('instance', 'N/A')}\n\n"
                            if result['status'] == 'SUCCESS' else "") +
                           (f"Troubleshooting Steps:\n" +
                            "\n".join(result.get('troubleshooting', []))
                            if result['status'] == 'FAILED' else "")
                }]
            }
        
        elif tool_name == "salesforce_query":
            if not ensure_connection():
                return {
                    "content": [{
                        "type": "text",
                        "text": "❌ Cannot establish Salesforce connection. Please run 'salesforce_connection_test' for detailed diagnostics."
                    }]
                }
                
            query = arguments.get('query')
            limit = arguments.get('limit', 200)
            
            if 'LIMIT' not in query.upper():
                query += f" LIMIT {min(limit, 2000)}"
            
            logging.info(f"Executing query: {query}")
            result = sf_conn.query_all(query)
            
            return {
                "content": [{
                    "type": "text",
                    "text": f"✓ Query returned {len(result['records'])} records:\n\n" + 
                           json.dumps(result['records'], indent=2)
                }]
            }
        
        elif tool_name == "salesforce_bulk_insert_simple":
            object_name = arguments.get('object_name')
            csv_data = arguments.get('csv_data')
            
            result = execute_bulk_insert_simple(object_name, csv_data)
            return {
                "content": [{
                    "type": "text",
                    "text": f"✓ Bulk Insert Results:\n" +
                           f"Object: {result.get('object_name', 'N/A')}\n" +
                           f"Total Records: {result.get('total_records', 0):,}\n" +
                           f"Successful: {result.get('success_count', 0):,}\n" +
                           f"Errors: {result.get('error_count', 0):,}\n\n" +
                           f"Message: {result.get('message', 'N/A')}" +
                           (f"\n\nErrors:\n" + "\n".join(result.get('errors', [])) if result.get('errors') else "")
                }]
            }
        
        elif tool_name == "salesforce_bulk_update_simple":
            object_name = arguments.get('object_name')
            csv_data = arguments.get('csv_data')
            
            result = execute_bulk_update_simple(object_name, csv_data)
            return {
                "content": [{
                    "type": "text",
                    "text": f"✓ Bulk Update Results:\n" +
                           f"Object: {result.get('object_name', 'N/A')}\n" +
                           f"Total Records: {result.get('total_records', 0):,}\n" +
                           f"Successful: {result.get('success_count', 0):,}\n" +
                           f"Errors: {result.get('error_count', 0):,}\n\n" +
                           f"Message: {result.get('message', 'N/A')}" +
                           (f"\n\nErrors:\n" + "\n".join(result.get('errors', [])) if result.get('errors') else "")
                }]
            }
        
        elif tool_name == "salesforce_bulk_upsert_simple":
            object_name = arguments.get('object_name')
            csv_data = arguments.get('csv_data')
            external_id_field = arguments.get('external_id_field')
            
            result = execute_bulk_upsert_simple(object_name, csv_data, external_id_field)
            return {
                "content": [{
                    "type": "text",
                    "text": f"✓ Bulk Upsert Results:\n" +
                           f"Object: {result.get('object_name', 'N/A')}\n" +
                           f"External ID Field: {result.get('external_id_field', 'N/A')}\n" +
                           f"Total Records: {result.get('total_records', 0):,}\n" +
                           f"Successful: {result.get('success_count', 0):,}\n" +
                           f"Errors: {result.get('error_count', 0):,}\n\n" +
                           f"Message: {result.get('message', 'N/A')}" +
                           (f"\n\nErrors:\n" + "\n".join(result.get('errors', [])) if result.get('errors') else "")
                }]
            }
        
        elif tool_name == "salesforce_count_records":
            if not ensure_connection():
                return {
                    "content": [{
                        "type": "text",
                        "text": "❌ Cannot establish Salesforce connection. Please run 'salesforce_connection_test' for detailed diagnostics."
                    }]
                }
                
            object_name = arguments.get('object_name')
            where_clause = arguments.get('where_clause', '')
            
            query = f"SELECT COUNT() FROM {object_name}"
            if where_clause:
                query += f" WHERE {where_clause}"
            
            result = sf_conn.query(query)
            return {
                "content": [{
                    "type": "text",
                    "text": f"✓ Object: {object_name}\n" +
                           f"Total records: {result['totalSize']:,}\n" +
                           f"Query: {query}"
                }]
            }
        
        elif tool_name == "salesforce_describe_object":
            object_name = arguments.get('object_name')
            
            result = describe_object(object_name)
            return {
                "content": [{
                    "type": "text",
                    "text": f"✓ Object Description: {result['object_name']}\n" +
                           f"Label: {result['label']}\n" +
                           f"Total Fields: {result['total_fields']}\n" +
                           f"Createable: {result['createable']}\n" +
                           f"Updateable: {result['updateable']}\n" +
                           f"Deletable: {result['deletable']}\n\n" +
                           f"Fields (first 10):\n" +
                           json.dumps(result['fields'][:10], indent=2)
                }]
            }
        
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Unknown tool: {tool_name}"
                }]
            }
            
    except Exception as e:
        logging.error(f"Tool execution error: {str(e)}")
        traceback.print_exc(file=sys.stderr)
        return {
            "content": [{
                "type": "text",
                "text": f"❌ Error executing {tool_name}: {str(e)}\n\nCheck the server logs for detailed error information."
            }]
        }

def do_initialize(params):
    """Initialize the MCP server."""
    logging.info("=== MCP Server initializing ===")
    
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "salesforce-bulk-mcp-server", "version": "2.2.0"}
    }

def do_initialized(params):
    """Handle initialized notification."""
    logging.info("=== MCP Server initialized and ready! ===")
    logging.info("Salesforce connection will be established on first tool call")
    return None

def main():
    """Main MCP server loop."""
    try:
        logging.info("=== Salesforce Bulk MCP Server with SSL Fix Starting ===")
        
        RPC_METHODS = {
            'initialize': do_initialize,
            'initialized': do_initialized,
            'tools/list': do_list_tools,
            'tools/call': do_call_tool,
        }

        # Main processing loop
        logging.info("MCP Server ready for requests...")
        
        for line_num, line in enumerate(sys.stdin, 1):
            try:
                line = line.strip()
                if not line:
                    continue
                
                request = json.loads(line)
                request_id = request.get('id')
                method_name = request.get('method')
                params = request.get('params', {})
                
                logging.debug(f"Received method: {method_name}")
                
                if method_name in RPC_METHODS:
                    try:
                        result = RPC_METHODS[method_name](params)
                        if request_id is not None:
                            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
                            print(json.dumps(response), flush=True)
                            logging.debug(f"Responded to {method_name}")
                    except Exception as e:
                        logging.error(f"Method error for {method_name}: {str(e)}")
                        traceback.print_exc(file=sys.stderr)
                        if request_id is not None:
                            response = {
                                "jsonrpc": "2.0", 
                                "id": request_id, 
                                "error": {"code": -32603, "message": str(e)}
                            }
                            print(json.dumps(response), flush=True)
                else:
                    logging.warning(f"Unknown method: {method_name}")
                            
            except json.JSONDecodeError as e:
                logging.error(f"JSON decode error on line {line_num}: {e}")
            except Exception as e:
                logging.error(f"Error on line {line_num}: {e}")
                traceback.print_exc(file=sys.stderr)
                
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        traceback.print_exc(file=sys.stderr)

if __name__ == '__main__':
    main()