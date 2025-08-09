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
import urllib.parse
from simple_salesforce import Salesforce
from dotenv import load_dotenv
import math
from datetime import datetime, timedelta

# Setup Logging
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# Global connection variable and test execution tracking
sf_conn = None
test_execution_cache = {}  # Track running tests by class names


def initialize_salesforce(retry_count=3):
    """Initialize Salesforce connection with authentication."""
    global sf_conn
    for attempt in range(retry_count):
        try:
            logging.info(
                f"=== Initializing Salesforce Connection (Attempt {attempt + 1}) ==="
            )
            # Load environment variables
            load_dotenv()
            logging.info("Environment variables loaded")

            # Get credentials
            username = os.getenv("SALESFORCE_USERNAME")
            password = os.getenv("SALESFORCE_PASSWORD")
            security_token = os.getenv("SALESFORCE_SECURITY_TOKEN")
            instance_url = os.getenv("SALESFORCE_INSTANCE_URL")

            # Log what we have (safely)
            logging.info(f"Username: {username[:10] + '***' if username else 'NOT SET'}")
            logging.info(
                f"Password: {'SET (' + str(len(password)) + ' chars)' if password else 'NOT SET'}"
            )
            logging.info(
                f"Security Token: {'SET (' + str(len(security_token)) + ' chars)' if security_token else 'NOT SET'}"
            )
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
                error_msg = (
                    f"Missing required Salesforce credentials: {', '.join(missing_creds)}"
                )
                logging.error(error_msg)
                return False, error_msg

            # Create a custom session for requests
            session = requests.Session()

            # Try to connect
            connection_params = {
                "username": username,
                "password": password,
                "security_token": security_token,
                "session": session,
            }

            if instance_url:
                connection_params["instance_url"] = instance_url

            logging.info("Attempting connection...")
            sf_conn = Salesforce(**connection_params)
            logging.info("Salesforce object created successfully")

            # Test the connection with a simple query (NO LIMIT with COUNT)
            logging.info("Testing connection with simple query...")
            sf_conn.query("SELECT COUNT(Id) FROM Account")
            logging.info("✓ Salesforce connection successful!")
            return True, "Connection successful."

        except Exception as e:
            error_msg = f"Salesforce connection attempt {attempt + 1} failed: {str(e)}"
            logging.error(error_msg)
            logging.error(f"Exception type: {type(e).__name__}")
            traceback.print_exc(file=sys.stderr)
            if attempt < retry_count - 1:
                logging.info("Retrying in 2 seconds...")
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
    try:
        # Simple connection test without LIMIT for COUNT queries
        sf_conn.query("SELECT COUNT(Id) FROM Account")
        return True
    except Exception as e:
        logging.warning(f"Existing connection invalid: {str(e)}, reconnecting...")
        sf_conn = None
        success, message = initialize_salesforce()
        return success


def check_object_exists(object_name):
    """Check if a custom object already exists."""
    try:
        if not object_name.endswith("__c"):
            object_name += "__c"
        # Query for existing object
        query = (
            f"SELECT Id FROM EntityDefinition WHERE QualifiedApiName = '{object_name}' LIMIT 1"
        )
        result = sf_conn.query(query)
        return len(result["records"]) > 0
    except Exception as e:
        logging.warning(f"Could not check if object exists: {str(e)}")
        return False


def normalize_soql_query(query):
    """Normalize SOQL queries to use proper COUNT syntax and handle LIMIT correctly."""
    # Fix COUNT() to COUNT(Id) for better compatibility
    if "COUNT()" in query.upper():
        query = query.replace("COUNT()", "COUNT(Id)")
        logging.info(f"Normalized COUNT() to COUNT(Id) in query: {query}")

    # Remove LIMIT from COUNT queries as they're not compatible
    if "COUNT(" in query.upper() and "LIMIT" in query.upper():
        # Remove LIMIT clause from COUNT queries
        import re
        query = re.sub(r"\s+LIMIT\s+\d+", "", query, flags=re.IGNORECASE)
        logging.info(f"Removed LIMIT from COUNT query: {query}")

    # Ensure proper FROM clause spacing
    query = " ".join(query.split())
    return query


def check_test_execution_status(class_names):
    """Check if specified test classes are currently running."""
    try:
        if not ensure_connection():
            return None

        # Check for running test queue items
        class_name_str = "','".join(class_names)
        queue_query = f"""
        SELECT Id, ApexClass.Name, Status, CreatedDate
        FROM ApexTestQueueItem
        WHERE ApexClass.Name IN ('{class_name_str}')
        AND Status IN ('Queued', 'Processing')
        ORDER BY CreatedDate DESC
        """
        queue_results = sf_conn.query(queue_query).get("records", [])

        if queue_results:
            running_classes = [r["ApexClass"]["Name"] for r in queue_results]
            return {
                "status": "running",
                "classes": running_classes,
                "queue_items": queue_results,
            }

        # Check for recent async jobs
        recent_time = (datetime.now() - timedelta(minutes=10)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        job_query = f"""
        SELECT Id, Status, CreatedDate, CompletedDate
        FROM AsyncApexJob
        WHERE CreatedDate >= {recent_time}
        AND Status IN ('Queued', 'Processing', 'Preparing')
        AND JobType = 'TestRequest'
        ORDER BY CreatedDate DESC
        LIMIT 5
        """
        job_results = sf_conn.query(job_query).get("records", [])

        if job_results:
            return {"status": "running", "jobs": job_results}

        return {"status": "not_running"}

    except Exception as e:
        logging.warning(f"Error checking test execution status: {str(e)}")
        return None


def get_recent_test_job_id(class_names):
    """Get the most recent test job ID for the specified classes."""
    try:
        if not ensure_connection():
            return None

        # Get the most recent completed queue items for these classes
        class_name_str = "','".join(class_names)
        queue_query = f"""
        SELECT Id, ParentJobId, ApexClass.Name, Status, CreatedDate
        FROM ApexTestQueueItem
        WHERE ApexClass.Name IN ('{class_name_str}')
        AND Status = 'Completed'
        ORDER BY CreatedDate DESC
        LIMIT 1
        """
        queue_results = sf_conn.query(queue_query).get("records", [])

        if queue_results and queue_results[0].get("ParentJobId"):
            job_id = queue_results[0]["ParentJobId"]
            logging.info(f"Found recent job ID for {class_names}: {job_id}")
            return job_id

        # Fallback: Look for recent async jobs
        recent_time = (datetime.now() - timedelta(hours=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        job_query = f"""
        SELECT Id, Status, CreatedDate, CompletedDate
        FROM AsyncApexJob
        WHERE CreatedDate >= {recent_time}
        AND Status = 'Completed'
        AND JobType = 'TestRequest'
        ORDER BY CreatedDate DESC
        LIMIT 1
        """
        job_results = sf_conn.query(job_query).get("records", [])

        if job_results:
            job_id = job_results[0]["Id"]
            logging.info(f"Found recent async job: {job_id}")
            return job_id

        return None

    except Exception as e:
        logging.warning(f"Error getting recent job ID: {str(e)}")
        return None


def query_tooling_api(query):
    """Query the Tooling API directly for coverage data."""
    try:
        if not ensure_connection():
            return None

        # Use Tooling API endpoint
        tooling_url = f"https://{sf_conn.sf_instance}/services/data/v{sf_conn.sf_version}/tooling/query/"
        headers = {
            "Authorization": f"Bearer {sf_conn.session_id}",
            "Content-Type": "application/json",
        }
        params = {"q": query}

        logging.info(f"Querying Tooling API: {tooling_url}")
        logging.info(f"Query: {query}")

        response = sf_conn.session.get(tooling_url, headers=headers, params=params)

        if response.status_code == 200:
            result = response.json()
            logging.info(
                f"Tooling API query successful: {len(result.get('records', []))} records returned"
            )
            return result
        else:
            logging.warning(
                f"Tooling API query failed: {response.status_code} - {response.text}"
            )
            return None

    except Exception as e:
        logging.warning(f"Tooling API query error: {str(e)}")
        return None


def get_comprehensive_coverage_data(job_id=None, class_names=None):
    """Get code coverage data using Tooling API and regular API with multiple approaches - FIXED VERSION."""
    coverage_results = []
    try:
        logging.info(f"Getting coverage data: job_id={job_id}, class_names={class_names}")

        # Method 1: If we have specific test class names, get coverage for the classes BEING TESTED (not the test classes themselves)
        if class_names and job_id:
            try:
                # Get coverage for classes tested BY these test classes, not the test classes themselves
                tooling_coverage_query = f"""
                SELECT ApexClassOrTrigger.Name, ApexClassOrTrigger.Id, NumLinesCovered, NumLinesUncovered, 
                       TestMethodName, ApexTestClass.Name as TestClassName
                FROM ApexCodeCoverage
                WHERE AsyncApexJobId = '{job_id}'
                AND ApexClassOrTrigger.Name != null
                ORDER BY ApexClassOrTrigger.Name, TestMethodName
                """

                tooling_coverage_result = query_tooling_api(tooling_coverage_query)
                if tooling_coverage_result and tooling_coverage_result.get("records"):
                    coverage_records = tooling_coverage_result["records"]
                    logging.info(f"Found {len(coverage_records)} detailed coverage records for job {job_id}")

                    # Aggregate coverage by the class being tested (not the test class)
                    class_coverage_map = {}
                    for record in coverage_records:
                        class_name = record.get("ApexClassOrTrigger", {}).get("Name", "Unknown")
                        test_class_name = record.get("TestClassName", "Unknown")
                        covered = record.get("NumLinesCovered", 0)
                        uncovered = record.get("NumLinesUncovered", 0)
                        
                        # Skip if this is a test class itself being measured
                        if class_name.endswith('Test') or class_name.endswith('_Test'):
                            continue
                            
                        logging.info(f"Coverage record: {class_name} tested by {test_class_name}: {covered}/{covered+uncovered} lines")

                        if class_name not in class_coverage_map:
                            class_coverage_map[class_name] = {
                                "ApexClassOrTrigger": {"Name": class_name},
                                "NumLinesCovered": covered,
                                "NumLinesUncovered": uncovered,
                                "TestMethods": [],
                            }
                        else:
                            # Take maximum coverage seen for this class
                            class_coverage_map[class_name]["NumLinesCovered"] = max(
                                class_coverage_map[class_name]["NumLinesCovered"], covered
                            )
                            class_coverage_map[class_name]["NumLinesUncovered"] = max(
                                class_coverage_map[class_name]["NumLinesUncovered"], uncovered
                            )

                        # Add test method info
                        method_name = record.get("TestMethodName", "Unknown")
                        class_coverage_map[class_name]["TestMethods"].append(
                            {
                                "method": f"{test_class_name}.{method_name}",
                                "covered": covered,
                                "uncovered": uncovered,
                            }
                        )

                    coverage_results = list(class_coverage_map.values())
                    if coverage_results:
                        logging.info(f"Aggregated coverage for {len(coverage_results)} classes from job {job_id}")
                        # Log the coverage details
                        for cov in coverage_results:
                            name = cov["ApexClassOrTrigger"]["Name"]
                            covered = cov["NumLinesCovered"]
                            uncovered = cov["NumLinesUncovered"]
                            total = covered + uncovered
                            pct = (covered / total * 100) if total > 0 else 0
                            logging.info(f"Final coverage for {name}: {pct:.1f}% ({covered}/{total})")
                        return coverage_results

            except Exception as e:
                logging.warning(f"Job-specific detailed coverage query failed: {str(e)}")

        # Method 2: Try job-specific ApexCodeCoverageAggregate
        if job_id:
            try:
                tooling_query = f"""
                SELECT ApexClassOrTrigger.Name, NumLinesCovered, NumLinesUncovered
                FROM ApexCodeCoverageAggregate
                WHERE AsyncApexJobId = '{job_id}'
                AND ApexClassOrTrigger.Name != null
                ORDER BY ApexClassOrTrigger.Name
                """

                tooling_result = query_tooling_api(tooling_query)
                if tooling_result and tooling_result.get("records"):
                    coverage_records = tooling_result["records"]
                    # Filter out test classes from aggregate results
                    filtered_records = []
                    for record in coverage_records:
                        class_name = record.get("ApexClassOrTrigger", {}).get("Name", "")
                        if not (class_name.endswith('Test') or class_name.endswith('_Test')):
                            filtered_records.append(record)
                            covered = record.get("NumLinesCovered", 0)
                            uncovered = record.get("NumLinesUncovered", 0)
                            total = covered + uncovered
                            pct = (covered / total * 100) if total > 0 else 0
                            logging.info(f"Aggregate coverage for {class_name}: {pct:.1f}% ({covered}/{total})")
                    
                    if filtered_records:
                        logging.info(f"Found {len(filtered_records)} aggregate coverage records for job {job_id}")
                        return filtered_records

            except Exception as e:
                logging.warning(f"Job-specific aggregate coverage query failed: {str(e)}")

        # Method 3: Try recent coverage data using regular API
        try:
            # Get the most recent coverage data
            recent_time = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            
            recent_coverage_query = f"""
            SELECT ApexClassOrTrigger.Name, NumLinesCovered, NumLinesUncovered,
                   TestMethodName, ApexTestClass.Name as TestClassName, CreatedDate
            FROM ApexCodeCoverage
            WHERE CreatedDate >= {recent_time}
            AND ApexClassOrTrigger.Name != null
            ORDER BY ApexClassOrTrigger.Name, CreatedDate DESC
            LIMIT 200
            """

            recent_coverage = sf_conn.query_all(recent_coverage_query).get("records", [])
            if recent_coverage:
                logging.info(f"Found {len(recent_coverage)} recent coverage records")

                # Aggregate by class (take maximum coverage for each class)
                class_coverage = {}
                for record in recent_coverage:
                    class_name = record.get("ApexClassOrTrigger", {}).get("Name")
                    if class_name and not (class_name.endswith('Test') or class_name.endswith('_Test')):
                        covered = record.get("NumLinesCovered", 0)
                        uncovered = record.get("NumLinesUncovered", 0)

                        if class_name not in class_coverage:
                            class_coverage[class_name] = {
                                "ApexClassOrTrigger": {"Name": class_name},
                                "NumLinesCovered": covered,
                                "NumLinesUncovered": uncovered,
                            }
                        else:
                            # Take maximum coverage
                            class_coverage[class_name]["NumLinesCovered"] = max(
                                class_coverage[class_name]["NumLinesCovered"], covered
                            )
                            class_coverage[class_name]["NumLinesUncovered"] = max(
                                class_coverage[class_name]["NumLinesUncovered"], uncovered
                            )

                coverage_results = list(class_coverage.values())
                if coverage_results:
                    logging.info(f"Aggregated {len(coverage_results)} classes from recent coverage")
                    return coverage_results

        except Exception as e:
            logging.warning(f"Recent coverage query failed: {str(e)}")

        # Method 4: Fallback to general aggregate coverage
        try:
            general_query = """
            SELECT ApexClassOrTrigger.Name, NumLinesCovered, NumLinesUncovered
            FROM ApexCodeCoverageAggregate
            WHERE ApexClassOrTrigger.Name != null
            AND (NumLinesCovered > 0 OR NumLinesUncovered > 0)
            ORDER BY ApexClassOrTrigger.Name
            LIMIT 50
            """

            general_result = query_tooling_api(general_query)
            if general_result and general_result.get("records"):
                coverage_records = general_result["records"]
                # Filter out test classes
                filtered_records = []
                for record in coverage_records:
                    class_name = record.get("ApexClassOrTrigger", {}).get("Name", "")
                    if not (class_name.endswith('Test') or class_name.endswith('_Test')):
                        filtered_records.append(record)
                
                if filtered_records:
                    logging.info(f"Found {len(filtered_records)} general aggregate coverage records")
                    return filtered_records

        except Exception as e:
            logging.warning(f"General aggregate coverage query failed: {str(e)}")

    except Exception as e:
        logging.error(f"All coverage methods failed: {str(e)}")

    logging.warning("No coverage data found using any method")
    return []


def get_current_org_coverage():
    """Get current org-wide code coverage as a standalone tool."""
    try:
        if not ensure_connection():
            return {"success": False, "message": "❌ Salesforce connection failed"}

        coverage_results = get_comprehensive_coverage_data()

        if not coverage_results:
            return {
                "success": True,
                "message": "📈 **Code Coverage Information**\n\n⚠️ No coverage data currently available.\n\n💡 **To see coverage:**\n• Run tests in Developer Console\n• Use 'Run Apex Tests' with coverage enabled\n• Check Setup → Apex Test Execution",
            }

        # Calculate statistics
        total_lines = sum(
            c.get("NumLinesCovered", 0) + c.get("NumLinesUncovered", 0)
            for c in coverage_results
        )
        covered_lines = sum(c.get("NumLinesCovered", 0) for c in coverage_results)
        overall_coverage = (covered_lines / total_lines * 100) if total_lines > 0 else 0

        # Categorize classes by coverage
        high_coverage = []
        medium_coverage = []
        low_coverage = []

        for coverage in coverage_results:
            covered = coverage.get("NumLinesCovered", 0)
            uncovered = coverage.get("NumLinesUncovered", 0)
            total = covered + uncovered
            if total > 0:
                percentage = (covered / total) * 100
                class_name = coverage.get("ApexClassOrTrigger", {}).get("Name", "Unknown")
                coverage_display = (
                    f"{class_name}: {percentage:.1f}% ({covered}/{total} lines)"
                )

                if percentage >= 85:
                    high_coverage.append(f"🟢 {coverage_display}")
                elif percentage >= 75:
                    medium_coverage.append(f"🟡 {coverage_display}")
                else:
                    low_coverage.append(f"🔴 {coverage_display}")

        report_lines = [
            "📈 **Current Org Code Coverage**",
            "",
            "📊 **Overall Statistics:**",
            f"   • Total Classes: {len(coverage_results)}",
            f"   • Overall Coverage: {overall_coverage:.1f}%",
            f"   • Total Lines: {total_lines:,}",
            f"   • Covered Lines: {covered_lines:,}",
            f"   • Uncovered Lines: {total_lines - covered_lines:,}",
        ]

        if high_coverage:
            report_lines.extend(
                [f"\n🟢 **High Coverage (≥85%):** {len(high_coverage)} classes"]
                + [f"   {item}" for item in high_coverage[:10]]
            )
            if len(high_coverage) > 10:
                report_lines.append(f"   ... and {len(high_coverage) - 10} more")

        if medium_coverage:
            report_lines.extend(
                [f"\n🟡 **Medium Coverage (75-84%):** {len(medium_coverage)} classes"]
                + [f"   {item}" for item in medium_coverage[:5]]
            )
            if len(medium_coverage) > 5:
                report_lines.append(f"   ... and {len(medium_coverage) - 5} more")

        if low_coverage:
            report_lines.extend(
                [f"\n🔴 **Low Coverage (<75%):** {len(low_coverage)} classes"]
                + [f"   {item}" for item in low_coverage[:5]]
            )
            if len(low_coverage) > 5:
                report_lines.append(f"   ... and {len(low_coverage) - 5} more")

        if overall_coverage < 75:
            report_lines.extend(
                [
                    "\n⚠️ **Recommendations:**",
                    "   • Org coverage is below 75% deployment minimum",
                    "   • Focus on classes with low coverage",
                    "   • Add more test methods for uncovered code",
                ]
            )

        return {
            "success": True,
            "overall_coverage": round(overall_coverage, 1),
            "total_classes": len(coverage_results),
            "message": "\n".join(report_lines),
        }

    except Exception as e:
        return {"success": False, "message": f"❌ Error getting coverage: {str(e)}"}


def parse_line_number_from_stack_trace(stack_trace):
    """Extract line number from stack trace."""
    if not stack_trace:
        return None
    try:
        # Look for patterns like "line 123" or ":123:"
        import re
        # Pattern 1: "line 123"
        line_match = re.search(r"line (\d+)", stack_trace, re.IGNORECASE)
        if line_match:
            return int(line_match.group(1))

        # Pattern 2: ":123:" (line number between colons)
        colon_match = re.search(r":(\d+):", stack_trace)
        if colon_match:
            return int(colon_match.group(1))

        # Pattern 3: Class.method:123
        method_line_match = re.search(r"\.[\w]+:(\d+)", stack_trace)
        if method_line_match:
            return int(method_line_match.group(1))

    except Exception as e:
        logging.warning(f"Error parsing line number from stack trace: {str(e)}")
    return None


def get_test_results_for_classes(class_names, job_id=None):
    """Get detailed test results for specific classes."""
    try:
        if not ensure_connection():
            return []

        # If no job_id provided, try to find the most recent one
        if not job_id:
            job_id = get_recent_test_job_id(class_names)

        test_results = []

        # Method 1: Try with job_id if available
        if job_id:
            results_query = f"""
            SELECT ApexClass.Name, MethodName, Outcome, Message, StackTrace,
                   RunTime, TestTimestamp
            FROM ApexTestResult
            WHERE AsyncApexJobId = '{job_id}'
            AND ApexClass.Name IN ('{"','".join(class_names)}')
            ORDER BY ApexClass.Name, MethodName
            """
            test_results = sf_conn.query_all(results_query).get("records", [])
            if test_results:
                logging.info(f"Found {len(test_results)} test results for job {job_id}")
                return test_results

        # Method 2: Try to get recent test results without job_id
        recent_time = (datetime.now() - timedelta(hours=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        class_name_str = "','".join(class_names)
        recent_results_query = f"""
        SELECT ApexClass.Name, MethodName, Outcome, Message, StackTrace,
               RunTime, TestTimestamp
        FROM ApexTestResult
        WHERE ApexClass.Name IN ('{class_name_str}')
        AND TestTimestamp >= {recent_time}
        ORDER BY TestTimestamp DESC, ApexClass.Name, MethodName
        LIMIT 50
        """
        test_results = sf_conn.query_all(recent_results_query).get("records", [])
        if test_results:
            logging.info(f"Found {len(test_results)} recent test results")
            return test_results

        logging.warning(f"No test results found for classes: {class_names}")
        return []

    except Exception as e:
        logging.warning(f"Error getting test results: {str(e)}")
        return []


def check_test_status_and_coverage(class_names=None):
    """Check test execution status and get coverage if tests are complete - with enhanced failure reporting."""
    try:
        if not ensure_connection():
            return {"success": False, "message": "❌ Salesforce connection failed"}

        # Check if tests are currently running
        if class_names:
            test_status = check_test_execution_status(class_names)
            if test_status and test_status["status"] == "running":
                running_classes = test_status.get("classes", [])
                return {
                    "success": True,
                    "status": "running",
                    "message": f"⏳ **Tests are currently running!**\n\n🔄 **Running Classes:**\n"
                    + "\n".join([f"• {cls}" for cls in running_classes])
                    + "\n\n💡 **Please wait for completion.** Check Setup → Apex Test Execution for progress.",
                }

        # Tests are not running, get current coverage AND test results
        if class_names:
            # Get the most recent job ID for these classes
            job_id = get_recent_test_job_id(class_names)

            # Get test results to check for failures
            test_results = get_test_results_for_classes(class_names, job_id)

            # Get coverage for specific classes
            coverage_data = get_comprehensive_coverage_data(job_id, class_names=class_names)

            # Build the response with test results AND coverage
            response_lines = [
                f"📈 **Test Results and Coverage for: {', '.join(class_names)}**",
                "",
            ]

            # Add test execution summary if we have test results
            if test_results:
                total_tests = len(test_results)
                passed_tests = len([r for r in test_results if r.get("Outcome") == "Pass"])
                failed_tests = len([r for r in test_results if r.get("Outcome") == "Fail"])

                if job_id:
                    response_lines.append(f"🧪 **Test Execution Results** (Job: {job_id}):")
                else:
                    response_lines.append("🧪 **Recent Test Execution Results:**")

                response_lines.extend(
                    [
                        f"   • Total Tests: {total_tests}",
                        f"   • ✅ Passed: {passed_tests}",
                        f"   • ❌ Failed: {failed_tests}",
                        f"   • Success Rate: {(passed_tests/max(total_tests,1)*100):.1f}%",
                        "",
                    ]
                )

                # Show detailed failed tests with enhanced error information
                failed_test_details = [
                    r for r in test_results if r.get("Outcome") == "Fail"
                ]
                if failed_test_details:
                    response_lines.append("❌ **Failed Tests (Detailed):**")
                    for i, failure in enumerate(
                        failed_test_details[:5]
                    ):  # Show up to 5 failures
                        class_name = failure.get("ApexClass", {}).get("Name", "Unknown")
                        method_name = failure.get("MethodName", "Unknown")
                        message = failure.get("Message", "No message")
                        stack_trace = failure.get("StackTrace", "")
                        run_time = failure.get("RunTime", 0)

                        # Extract line number from stack trace
                        line_number = parse_line_number_from_stack_trace(stack_trace)

                        response_lines.append(f"\n**{i+1}. {class_name}.{method_name}**")

                        # Add line number if found
                        if line_number:
                            response_lines.append(f"   📍 **Line:** {line_number}")

                        # Add runtime if available
                        if run_time:
                            response_lines.append(f"   ⏱️ **Runtime:** {run_time}ms")

                        # Add error message (truncated if too long)
                        if message:
                            if len(message) > 300:
                                truncated_message = message[:300] + "..."
                                response_lines.append(
                                    f"   🚨 **Error:** {truncated_message}"
                                )
                                response_lines.append(
                                    "   💡 *Message truncated - check Salesforce for complete details*"
                                )
                            else:
                                response_lines.append(f"   🚨 **Error:** {message}")

                        # Add relevant parts of stack trace
                        if stack_trace:
                            # Extract the most relevant line from stack trace
                            stack_lines = stack_trace.split("\n")
                            relevant_lines = []
                            for line in stack_lines[:2]:  # Show first 2 lines of stack trace
                                line = line.strip()
                                if (
                                    line
                                    and not line.startswith("System.")
                                    and not line.startswith("caused by")
                                ):
                                    relevant_lines.append(line)

                            if relevant_lines:
                                response_lines.append("   📋 **Stack Trace:**")
                                for stack_line in relevant_lines:
                                    if len(stack_line) > 80:
                                        stack_line = stack_line[:80] + "..."
                                    response_lines.append(f"     {stack_line}")

                        # Add separator between failures
                        if i < len(failed_test_details) - 1 and i < 4:
                            response_lines.append("   " + "─" * 50)

                    if len(failed_test_details) > 5:
                        response_lines.append(
                            f"\n   💡 **Note:** Showing first 5 failures. {len(failed_test_details) - 5} more failures occurred."
                        )

                    response_lines.append("")

            # Add coverage information
            if coverage_data:
                response_lines.append("📊 **Code Coverage:**")
                total_covered = 0
                total_lines = 0
                found_coverage = False

                for coverage in coverage_data:
                    class_name = coverage.get("ApexClassOrTrigger", {}).get(
                        "Name", "Unknown"
                    )
                    covered = coverage.get("NumLinesCovered", 0)
                    uncovered = coverage.get("NumLinesUncovered", 0)
                    total = covered + uncovered

                    if total > 0:
                        percentage = (covered / total) * 100
                        status = "🟢" if percentage >= 75 else "🟡" if percentage >= 50 else "🔴"
                        response_lines.append(
                            f"   {status} **{class_name}**: {percentage:.1f}% ({covered}/{total} lines)"
                        )
                        total_covered += covered
                        total_lines += total
                        found_coverage = True

                        # Show test method details if available
                        if "TestMethods" in coverage:
                            for method in coverage["TestMethods"][:3]:  # Show first 3
                                method_total = method["covered"] + method["uncovered"]
                                if method_total > 0:
                                    method_pct = (
                                        method["covered"] / method_total
                                    ) * 100
                                    response_lines.append(
                                        f"     • {method['method']}: {method_pct:.1f}%"
                                    )

                if found_coverage:
                    overall_pct = (
                        (total_covered / total_lines * 100) if total_lines > 0 else 0
                    )
                    response_lines.extend(
                        [
                            "",
                            f"📊 **Overall Coverage**: {overall_pct:.1f}% ({total_covered}/{total_lines} lines)",
                            "",
                            "💡 **Coverage Legend:**",
                            "   🟢 Good (≥75%) | 🟡 Fair (50-74%) | 🔴 Poor (<50%)",
                        ]
                    )
                else:
                    response_lines.append("   ℹ️ No coverage data available")
            else:
                response_lines.append("📊 **Code Coverage:** No data available")

            # Add recommendations if there were failures
            if test_results:
                failed_tests = len([r for r in test_results if r.get("Outcome") == "Fail"])
                if failed_tests > 0:
                    response_lines.extend(
                        [
                            "",
                            "💡 **Recommendations:**",
                            "   • Review failed test details above",
                            "   • Check the specific line numbers where failures occurred",
                            "   • Fix the underlying code issues causing test failures",
                            "   • Re-run tests after making corrections",
                        ]
                    )

            return {
                "success": True,
                "status": "complete_with_details" if test_results else "coverage_only",
                "message": "\n".join(response_lines),
            }

        else:
            # Return overall org coverage
            return get_current_org_coverage()

    except Exception as e:
        return {
            "success": False,
            "message": f"❌ Error checking test status and coverage: {str(e)}",
        }


def run_apex_tests_comprehensive(
    class_names=None,
    test_level="RunSpecifiedTests",
    async_execution=False,
    code_coverage=True,
    verbose=False,
):
    """Run Apex tests with comprehensive options and code coverage - FIXED VERSION."""
    try:
        if not ensure_connection():
            return {"success": False, "message": "❌ Salesforce connection failed"}

        logging.info(
            f"Running comprehensive Apex tests: level={test_level}, classes={class_names}, coverage={code_coverage}"
        )

        # Validate test level and class names
        if test_level == "RunSpecifiedTests" and not class_names:
            return {
                "success": False,
                "message": "❌ Test level 'RunSpecifiedTests' requires class names to be specified",
            }

        # Check if tests are already running for these classes
        if class_names:
            test_status = check_test_execution_status(class_names)
            if test_status and test_status["status"] == "running":
                running_classes = test_status.get("classes", [])
                return {
                    "success": False,
                    "message": f"⏳ **Tests are already running!**\n\n🔄 **Currently Running Classes:**\n"
                    + "\n".join([f"• {cls}" for cls in running_classes])
                    + "\n\n💡 **Please wait for completion before running tests again.**\n"
                    + "Check Setup → Apex Test Execution for progress.",
                }

        if class_names:
            # Verify test classes exist
            class_name_str = "','".join(class_names)
            class_query = (
                f"SELECT Id, Name FROM ApexClass WHERE Name IN ('{class_name_str}')"
            )
            class_records = sf_conn.query(class_query).get("records", [])

            if not class_records:
                return {
                    "success": False,
                    "message": f"❌ No test classes found with names: {', '.join(class_names)}",
                }

            found_classes = [rec["Name"] for rec in class_records]
            class_ids = [rec["Id"] for rec in class_records]
            logging.info(f"Found test classes: {found_classes}")

        # FIXED: Try direct test enqueueing first (this is what was working before)
        if class_names and test_level == "RunSpecifiedTests":
            result = enqueue_tests_individually(class_names, code_coverage)
            if result["success"]:
                return result

        # Try Tooling API approaches
        result = run_apex_tests_tooling_api_enhanced(
            class_names, test_level, async_execution, code_coverage
        )
        if result["success"]:
            return result

        # Fallback to analysis mode
        return run_apex_tests_analysis_mode(class_names, test_level, code_coverage)

    except Exception as e:
        error_message = (
            f"❌ An exception occurred during comprehensive test execution: {str(e)}"
        )
        logging.error(error_message)
        traceback.print_exc(file=sys.stderr)
        return {"success": False, "message": error_message}


def run_apex_tests_tooling_api_enhanced(
    class_names, test_level, async_execution, code_coverage):
    """Enhanced Tooling API approach with proper payload structure."""
    try:
        logging.info("Attempting enhanced Tooling API test execution")

        # Build proper test payload based on reference code structure
        if test_level == "RunSpecifiedTests" and class_names:
            class_name_str = "','".join(class_names)
            class_query = f"SELECT Id FROM ApexClass WHERE Name IN ('{class_name_str}')"
            class_records = sf_conn.query(class_query).get("records", [])
            class_ids = [rec["Id"] for rec in class_records]

            payload = {
                "testLevel": test_level,
                "classIds": ",".join(class_ids) if class_ids else "",
                "skipCodeCoverage": not code_coverage,
            }
        elif test_level == "RunLocalTests":
            payload = {
                "testLevel": "RunLocalTests",
                "skipCodeCoverage": not code_coverage,
            }
        elif test_level == "RunAllTestsInOrg":
            payload = {
                "testLevel": "RunAllTestsInOrg",
                "skipCodeCoverage": not code_coverage,
            }
        else:
            return {"success": False, "message": f"❌ Invalid test level: {test_level}"}

        # Try enhanced Tooling API endpoints
        endpoints = [
            f"https://{sf_conn.sf_instance}/services/data/v{sf_conn.sf_version}/tooling/runTestsAsynchronous",
            f"https://{sf_conn.sf_instance}/services/data/v{sf_conn.sf_version}/tooling/runTestsSynchronous",
        ]

        headers = {
            "Authorization": f"Bearer {sf_conn.session_id}",
            "Content-Type": "application/json",
        }

        for endpoint in endpoints:
            try:
                logging.info(f"Trying enhanced endpoint: {endpoint}")
                logging.info(f"Payload: {json.dumps(payload, indent=2)}")

                response = sf_conn.session.post(endpoint, headers=headers, json=payload)

                if response.status_code in [200, 201]:
                    result_data = response.json()
                    logging.info(f"Success! Response: {result_data}")

                    if "runTestsAsynchronous" in endpoint:
                        # Async execution - return job ID for monitoring
                        job_id = (
                            result_data
                            if isinstance(result_data, str)
                            else result_data.get("id", result_data)
                        )

                        if async_execution:
                            return {
                                "success": True,
                                "execution_method": "Tooling_API_Async",
                                "job_id": job_id,
                                "test_level": test_level,
                                "classes": class_names or "All",
                                "message": f"✅ Apex tests started successfully!\n\n📋 **Test Execution Details:**\n• Test Level: {test_level}\n• Classes: {', '.join(class_names) if class_names else 'All tests'}\n• Job ID: {job_id}\n• Code Coverage: {'Enabled' if code_coverage else 'Disabled'}\n\n💡 **Monitor Progress:** Check Setup → Apex Test Execution in Salesforce",
                            }
                        else:
                            # Wait for completion and get results
                            return monitor_test_execution(
                                job_id, code_coverage, class_names, test_level
                            )
                    else:
                        # Synchronous execution - parse immediate results
                        return parse_test_results(
                            result_data,
                            code_coverage,
                            class_names,
                            test_level,
                            "Tooling_API_Sync",
                        )
                else:
                    logging.warning(
                        f"Endpoint {endpoint} failed with status {response.status_code}: {response.text}"
                    )

            except Exception as e:
                logging.warning(f"Endpoint {endpoint} failed with error: {str(e)}")
                continue

        return {"success": False, "message": "❌ All enhanced Tooling API endpoints failed"}

    except Exception as e:
        logging.warning(f"Enhanced Tooling API method failed: {str(e)}")
        return {"success": False, "message": f"❌ Enhanced Tooling API failed: {str(e)}"}


def enqueue_tests_individually(class_names, code_coverage):
    """Enqueue test classes individually using ApexTestQueueItem - FIXED VERSION."""
    try:
        logging.info(f"Enqueueing tests individually for classes: {class_names}")

        # Get class IDs
        class_name_str = "','".join(class_names)
        class_query = (
            f"SELECT Id, Name FROM ApexClass WHERE Name IN ('{class_name_str}')"
        )
        class_records = sf_conn.query(class_query).get("records", [])

        if not class_records:
            return {
                "success": False,
                "message": f"❌ Test classes not found: {', '.join(class_names)}",
            }

        enqueued_items = []
        for class_record in class_records:
            try:
                # Create ApexTestQueueItem for each class
                queue_item = {"ApexClassId": class_record["Id"]}
                result = sf_conn.ApexTestQueueItem.create(queue_item)

                if result.get("success"):
                    enqueued_items.append(
                        {
                            "class_name": class_record["Name"],
                            "class_id": class_record["Id"],
                            "queue_id": result.get("id"),
                        }
                    )
                    logging.info(
                        f"Enqueued test class {class_record['Name']} with queue ID {result.get('id')}"
                    )

            except Exception as e:
                logging.warning(
                    f"Failed to enqueue class {class_record['Name']}: {str(e)}"
                )

        if enqueued_items:
            return {
                "success": True,
                "execution_method": "ApexTestQueueItem",
                "enqueued_classes": len(enqueued_items),
                "queue_items": enqueued_items,
                "message": f"✅ Successfully enqueued {len(enqueued_items)} test classes!\n\n📋 **Enqueued Tests:**\n"
                + "\n".join(
                    [
                        f"• {item['class_name']} (Queue ID: {item['queue_id']})"
                        for item in enqueued_items
                    ]
                )
                + f"\n\n💡 **Monitor Progress:** Check Setup → Apex Test Execution or query ApexTestQueueItem\n\n⏳ **Note:** Tests are now running. Use 'check coverage' to see results once complete.",
            }
        else:
            return {"success": False, "message": "❌ Failed to enqueue any test classes"}

    except Exception as e:
        return {"success": False, "message": f"❌ Individual enqueueing failed: {str(e)}"}


def monitor_test_execution(job_id, code_coverage, class_names, test_level):
    """Monitor async test execution and return results."""
    try:
        logging.info(f"Monitoring test execution for job ID: {job_id}")

        # Poll for completion (simplified version)
        max_polls = 60  # 5 minutes max
        poll_interval = 5

        for poll in range(max_polls):
            try:
                # Check job status
                status_query = (
                    f"SELECT Status, CompletedDate FROM AsyncApexJob WHERE Id = '{job_id}'"
                )
                status_result = sf_conn.query(status_query).get("records", [])

                if status_result:
                    status = status_result[0]["Status"]
                    logging.info(f"Job {job_id} status: {status}")

                    if status in ["Completed", "Failed", "Aborted"]:
                        # Get test results
                        return get_test_results_by_job_id(
                            job_id, code_coverage, class_names, test_level
                        )

                time.sleep(poll_interval)

            except Exception as e:
                logging.warning(f"Error polling job status: {str(e)}")
                break

        return {
            "success": True,
            "execution_method": "Async_Timeout",
            "job_id": job_id,
            "message": f"⏱️ Test execution is taking longer than expected.\n\n📋 **Job ID:** {job_id}\n\n💡 **Check Status Manually:** Go to Setup → Apex Test Execution in Salesforce",
        }

    except Exception as e:
        return {
            "success": False,
            "message": f"❌ Error monitoring test execution: {str(e)}",
        }


def get_test_results_by_job_id(job_id, code_coverage, class_names, test_level):
    """Get comprehensive test results by job ID with enhanced failure details."""
    try:
        logging.info(f"Getting test results for job ID: {job_id}")

        # Get detailed test results with stack trace and line number information
        results_query = f"""
        SELECT ApexClass.Name, MethodName, Outcome, Message, StackTrace,
               RunTime, TestTimestamp
        FROM ApexTestResult
        WHERE AsyncApexJobId = '{job_id}'
        ORDER BY ApexClass.Name, MethodName
        """
        test_results = sf_conn.query_all(results_query).get("records", [])

        # Get code coverage if requested
        coverage_results = []
        if code_coverage:
            coverage_results = get_comprehensive_coverage_data(job_id, class_names)

        return format_comprehensive_test_results(
            test_results,
            coverage_results,
            job_id,
            class_names,
            test_level,
            "Async_Complete",
        )

    except Exception as e:
        return {"success": False, "message": f"❌ Error getting test results: {str(e)}"}


def format_comprehensive_test_results(
    test_results, coverage_results, job_id, class_names, test_level, execution_method):
    """Enhanced formatting with detailed failure information and line numbers."""
    try:
        # If no coverage provided, try to get it
        if not coverage_results and job_id:
            coverage_results = get_comprehensive_coverage_data(job_id, class_names)

        # Calculate summary statistics
        total_tests = len(test_results)
        passed_tests = len([r for r in test_results if r.get("Outcome") == "Pass"])
        failed_tests = len([r for r in test_results if r.get("Outcome") == "Fail"])

        # Calculate overall coverage
        total_lines = sum(
            c.get("NumLinesCovered", 0) + c.get("NumLinesUncovered", 0)
            for c in coverage_results
        )
        covered_lines = sum(c.get("NumLinesCovered", 0) for c in coverage_results)
        overall_coverage = (covered_lines / total_lines * 100) if total_lines > 0 else 0

        # Build comprehensive report
        report_lines = [
            "🧪 **Apex Test Execution Complete**",
            "",
            "📊 **Summary:**",
            f"   • Test Level: {test_level}",
            f"   • Total Tests: {total_tests}",
            f"   • ✅ Passed: {passed_tests}",
            f"   • ❌ Failed: {failed_tests}",
            f"   • Success Rate: {(passed_tests/max(total_tests,1)*100):.1f}%",
            f"   • Job ID: {job_id}",
            f"   • Execution Method: {execution_method}",
        ]

        if coverage_results:
            report_lines.extend(
                [
                    f"   • 📈 Overall Coverage: {overall_coverage:.1f}%",
                    f"   • Covered Lines: {covered_lines:,}",
                    f"   • Total Lines: {total_lines:,}",
                    f"   • Classes with Coverage: {len(coverage_results)}",
                ]
            )
        else:
            report_lines.append("   • 📈 Coverage Data: Not available")

        # Show detailed failed tests with enhanced error information
        failed_test_details = [r for r in test_results if r.get("Outcome") == "Fail"]
        if failed_test_details:
            report_lines.append("\n❌ **Failed Tests (Detailed):**")
            for i, failure in enumerate(failed_test_details[:10]):  # Show up to 10 failures
                class_name = failure.get("ApexClass", {}).get("Name", "Unknown")
                method_name = failure.get("MethodName", "Unknown")
                message = failure.get("Message", "No message")
                stack_trace = failure.get("StackTrace", "")
                run_time = failure.get("RunTime", 0)

                # Extract line number from stack trace
                line_number = parse_line_number_from_stack_trace(stack_trace)

                report_lines.append(f"\n**{i+1}. {class_name}.{method_name}**")

                # Add line number if found
                if line_number:
                    report_lines.append(f"   📍 **Line:** {line_number}")

                # Add runtime if available
                if run_time:
                    report_lines.append(f"   ⏱️ **Runtime:** {run_time}ms")

                # Add error message (truncated if too long)
                if message:
                    if len(message) > 200:
                        truncated_message = message[:200] + "..."
                        report_lines.append(f"   🚨 **Error:** {truncated_message}")
                        report_lines.append(
                            f"   💡 *Full message truncated - check Salesforce for complete details*"
                        )
                    else:
                        report_lines.append(f"   🚨 **Error:** {message}")

                # Add relevant parts of stack trace
                if stack_trace:
                    # Extract the most relevant line from stack trace
                    stack_lines = stack_trace.split("\n")
                    relevant_lines = []
                    for line in stack_lines[:3]:  # Show first 3 lines of stack trace
                        line = line.strip()
                        if line and not line.startswith("System.") and not line.startswith(
                            "caused by"
                        ):
                            relevant_lines.append(line)

                    if relevant_lines:
                        report_lines.append("   📋 **Stack Trace:**")
                        for stack_line in relevant_lines:
                            if len(stack_line) > 100:
                                stack_line = stack_line[:100] + "..."
                            report_lines.append(f"     {stack_line}")

                # Add separator between failures
                if i < len(failed_test_details) - 1 and i < 9:
                    report_lines.append("   " + "─" * 50)

            if len(failed_test_details) > 10:
                report_lines.append(
                    f"\n   💡 **Note:** Showing first 10 failures. {len(failed_test_details) - 10} more failures occurred."
                )
                report_lines.append(
                    "   📋 **For complete details:** Check Setup → Apex Test Execution in Salesforce"
                )

        # Show successful tests if verbose or if there are few tests
        passed_test_details = [r for r in test_results if r.get("Outcome") == "Pass"]
        if passed_test_details and (total_tests <= 10 or failed_tests == 0):
            report_lines.append(f"\n✅ **Passed Tests:** ({len(passed_test_details)})")
            for i, success in enumerate(passed_test_details[:5]):  # Show first 5 passed tests
                class_name = success.get("ApexClass", {}).get("Name", "Unknown")
                method_name = success.get("MethodName", "Unknown")
                run_time = success.get("RunTime", 0)
                runtime_display = f" ({run_time}ms)" if run_time else ""
                report_lines.append(f"   • {class_name}.{method_name}{runtime_display}")

            if len(passed_test_details) > 5:
                report_lines.append(
                    f"   ... and {len(passed_test_details) - 5} more passed tests"
                )

        # Show coverage details with better formatting
        if coverage_results:
            report_lines.append("\n📈 **Code Coverage Details:**")

            # Sort by coverage percentage
            coverage_with_percentage = []
            for coverage in coverage_results:
                covered = coverage.get("NumLinesCovered", 0)
                uncovered = coverage.get("NumLinesUncovered", 0)
                total = covered + uncovered
                if total > 0:
                    percentage = (covered / total) * 100
                    coverage_with_percentage.append(
                        {
                            "name": coverage.get("ApexClassOrTrigger", {}).get(
                                "Name", "Unknown"
                            ),
                            "covered": covered,
                            "total": total,
                            "percentage": percentage,
                        }
                    )

            # Sort by percentage (lowest first to highlight problem areas)
            coverage_with_percentage.sort(key=lambda x: x["percentage"])

            for i, cov in enumerate(coverage_with_percentage[:15]):  # Show first 15
                status = (
                    "🔴"
                    if cov["percentage"] < 75
                    else "🟡"
                    if cov["percentage"] < 85
                    else "🟢"
                )
                report_lines.append(
                    f"   {status} {cov['name']}: {cov['percentage']:.1f}% ({cov['covered']}/{cov['total']} lines)"
                )

            if len(coverage_with_percentage) > 15:
                report_lines.append(
                    f"   ... and {len(coverage_with_percentage) - 15} more classes"
                )

            # Add coverage insights
            low_coverage = [c for c in coverage_with_percentage if c["percentage"] < 75]
            if low_coverage:
                report_lines.append("\n⚠️ **Coverage Insights:**")
                report_lines.append(f"   • {len(low_coverage)} classes have <75% coverage")
                report_lines.append(
                    f"   • Focus on improving: {', '.join([c['name'] for c in low_coverage[:3]])}"
                )
        else:
            report_lines.extend(
                [
                    "\n📈 **Code Coverage:**",
                    "   ℹ️ Coverage data not available for this execution.",
                    "   💡 Try using 'check coverage' tool to see current org coverage.",
                ]
            )

        # Add recommendations based on results
        if failed_tests > 0:
            report_lines.extend(
                [
                    "\n💡 **Recommendations:**",
                    "   • Review failed test details above",
                    "   • Check the specific line numbers where failures occurred",
                    "   • Fix the underlying code issues causing test failures",
                    "   • Re-run tests after making corrections",
                ]
            )

        return {
            "success": True,
            "execution_method": execution_method,
            "job_id": job_id,
            "summary": {
                "total_tests": total_tests,
                "passed_tests": passed_tests,
                "failed_tests": failed_tests,
                "overall_coverage": round(overall_coverage, 1),
                "coverage_classes": len(coverage_results),
            },
            "failed_tests": failed_test_details,  # Include raw failure data
            "message": "\n".join(report_lines),
        }

    except Exception as e:
        return {"success": False, "message": f"❌ Error formatting test results: {str(e)}"}


def run_apex_tests_analysis_mode(class_names, test_level, code_coverage):
    """Provide analysis when test execution is not available."""
    try:
        logging.info("Running in analysis mode - test execution not available")

        analysis_lines = [
            "🧪 **Apex Test Analysis Mode**",
            "",
            "📊 **Request Details:**",
            f"   • Test Level: {test_level}",
            f"   • Classes: {', '.join(class_names) if class_names else 'All'}",
            f"   • Code Coverage: {'Requested' if code_coverage else 'Not requested'}",
            "",
        ]

        class_records = []
        if class_names:
            # Analyze specified test classes
            class_name_str = "','".join(class_names)
            class_query = f"SELECT Id, Name, LengthWithoutComments, CreatedDate FROM ApexClass WHERE Name IN ('{class_name_str}')"
            class_records = sf_conn.query(class_query).get("records", [])

            if class_records:
                analysis_lines.append("✅ **Found Test Classes:**")
                for cls in class_records:
                    analysis_lines.append(
                        f"   • {cls['Name']} (ID: {cls['Id']}, Length: {cls.get('LengthWithoutComments', 'Unknown')} lines)"
                    )
            else:
                analysis_lines.append("❌ **Test Classes Not Found:**")
                for name in class_names:
                    analysis_lines.append(f"   • {name}")

        analysis_lines.extend(
            [
                "",
                "⚠️ **Note:** Automated test execution is not available in this org type.",
                "",
                "🔧 **Manual Execution Options:**",
                "   1. **Developer Console:**",
                "      • Open Developer Console",
                "      • Go to Test → New Run",
                f"      • Select test level: {test_level}",
                f"      • {'Select classes: ' + ', '.join(class_names) if class_names else 'Run all tests'}",
                "      • Enable 'Calculate Code Coverage' if needed",
                "",
                "   2. **VS Code:**",
                "      • Open Command Palette (Ctrl/Cmd + Shift + P)",
                "      • Type 'SFDX: Run Apex Tests'",
                "      • Choose appropriate option",
                "",
                "   3. **SFDX CLI:**",
                f"      • {'sfdx force:apex:test:run -n ' + ','.join(class_names) + ' -c' if class_names else 'sfdx force:apex:test:run -l ' + test_level + ' -c'}",
            ]
        )

        return {
            "success": True,
            "execution_method": "Analysis_Mode",
            "test_level": test_level,
            "classes_found": len(class_records) if class_names else 0,
            "message": "\n".join(analysis_lines),
        }

    except Exception as e:
        return {"success": False, "message": f"❌ Analysis mode failed: {str(e)}"}


def parse_test_results(
    result_data, code_coverage, class_names, test_level, execution_method):
    """Parse and format test results from API response."""
    try:
        # This would parse the actual API response format
        # Implementation depends on the exact response structure
        return {
            "success": True,
            "execution_method": execution_method,
            "message": f"✅ Test execution completed!\n\nRaw Results: {json.dumps(result_data, indent=2)}",
        }
    except Exception as e:
        return {"success": False, "message": f"❌ Error parsing results: {str(e)}"}


def get_object_required_fields(object_name):
    """Get required fields for an object."""
    try:
        if not ensure_connection():
            return {"success": False, "message": "❌ Salesforce connection failed"}

        describe_result = getattr(sf_conn, object_name).describe()
        required_fields = []

        for field in describe_result["fields"]:
            # Field is required if it's not nillable, not auto-generated, and createable
            if (
                not field["nillable"]
                and not field["defaultedOnCreate"]
                and field["createable"]
                and field["name"]
                not in [
                    "Id",
                    "CreatedDate",
                    "CreatedById",
                    "LastModifiedDate",
                    "LastModifiedById",
                    "SystemModstamp",
                ]
            ):
                field_info = {
                    "name": field["name"],
                    "label": field["label"],
                    "type": field["type"],
                    "length": field.get("length"),
                    "picklistValues": [
                        pv["value"] for pv in field.get("picklistValues", [])
                    ],
                }

                # Add sample/format information for different field types
                if field["type"] == "date":
                    field_info["format"] = "YYYY-MM-DD (e.g., 2024-12-31)"
                elif field["type"] == "datetime":
                    field_info[
                        "format"
                    ] = "YYYY-MM-DDTHH:MM:SS (e.g., 2024-12-31T23:59:59)"
                elif field["type"] == "email":
                    field_info[
                        "format"
                    ] = "Valid email address (e.g., user@example.com)"
                elif field["type"] == "phone":
                    field_info["format"] = "Phone number (e.g., +1-555-123-4567)"
                elif field["type"] == "url":
                    field_info["format"] = "Valid URL (e.g., https://example.com)"

                required_fields.append(field_info)

        return {
            "success": True,
            "object_name": object_name,
            "object_label": describe_result["label"],
            "required_fields": required_fields,
        }

    except Exception as e:
        return {"success": False, "message": f"❌ Failed to get required fields: {str(e)}"}


def create_records_with_validation(object_name, records_data):
    """Create records with validation that all required fields are provided."""
    try:
        if not ensure_connection():
            return {"success": False, "message": "❌ Salesforce connection failed"}

        if not records_data:
            return {"success": False, "message": "❌ No records provided"}

        # Get required fields for validation
        required_fields_result = get_object_required_fields(object_name)
        if not required_fields_result["success"]:
            return required_fields_result

        required_fields = required_fields_result["required_fields"]
        required_field_names = [field["name"] for field in required_fields]

        # Validate that all required fields are provided in each record
        validation_errors = []
        for i, record in enumerate(records_data):
            missing_fields = []
            for req_field in required_field_names:
                if (
                    req_field not in record
                    or record[req_field] is None
                    or record[req_field] == ""
                ):
                    missing_fields.append(req_field)

            if missing_fields:
                validation_errors.append(
                    f"Record {i+1}: Missing required fields: {', '.join(missing_fields)}"
                )

        if validation_errors:
            # Return detailed information about missing fields
            error_message = "❌ **Missing Required Fields**\n\n"
            error_message += "\n".join(validation_errors)
            error_message += (
                f"\n\n📋 **Required Fields for {required_fields_result['object_label']}:**\n"
            )

            for field in required_fields:
                field_desc = (
                    f"• **{field['label']}** ({field['name']}) - {field['type']}"
                )
                if field["type"] == "picklist" and field.get("picklistValues"):
                    field_desc += (
                        f"\n  Valid options: {', '.join(field['picklistValues'])}"
                    )
                elif field.get("format"):
                    field_desc += f"\n  Format: {field['format']}"
                elif field["type"] == "string" and field.get("length"):
                    field_desc += f" (max {field['length']} characters)"

                error_message += field_desc + "\n"

            error_message += "\n💡 **Please provide all required fields and try again.**"

            return {"success": False, "message": error_message}

        # All validation passed, create the records
        logging.info(f"Creating {len(records_data)} records in {object_name}")

        # Use bulk API if more than 1 record for efficiency
        if len(records_data) > 1:
            bulk_object = getattr(sf_conn.bulk, object_name)
            results = bulk_object.insert(records_data)

            success_count = sum(1 for r in results if r.get("success"))
            error_count = len(records_data) - success_count
            errors = [
                r.get("error", "Unknown error")
                for r in results
                if not r.get("success")
            ][:5]
            successful_ids = [r.get("id") for r in results if r.get("success")]

            return {
                "success": True,
                "total_records": len(records_data),
                "success_count": success_count,
                "error_count": error_count,
                "errors": errors,
                "record_ids": successful_ids,
                "message": f"✅ Bulk creation completed. Success: {success_count}, Errors: {error_count}",
            }
        else:
            # Single record creation
            sobject = getattr(sf_conn, object_name)
            result = sobject.create(records_data[0])

            if result.get("success", False):
                return {
                    "success": True,
                    "total_records": 1,
                    "success_count": 1,
                    "error_count": 0,
                    "record_ids": [result.get("id")],
                    "message": f"✅ Successfully created {object_name} record with ID: {result.get('id')}",
                }
            else:
                return {
                    "success": False,
                    "message": f"❌ Failed to create record: {result.get('errors', 'Unknown error')}",
                }

    except Exception as e:
        error_message = f"❌ An exception occurred during record creation: {str(e)}"
        logging.error(error_message)
        traceback.print_exc(file=sys.stderr)
        return {"success": False, "message": error_message}


def test_connection():
    """Test Salesforce connection and report connection details."""
    try:
        logging.info("=== Connection Test Requested ===")
        success, message = initialize_salesforce()

        if success:
            instance = sf_conn.sf_instance
            base_url = sf_conn.base_url
            version = sf_conn.sf_version

            details_message = (
                f"Connection Successful.\n\n"
                f"📋 **Connection Details:**\n"
                f"   - **Instance URL:** {instance}\n"
                f"   - **Base URL for API:** {base_url}\n"
                f"   - **API Version:** {version}"
            )

            return {"status": "SUCCESS", "message": details_message}
        else:
            return {"status": "FAILED", "message": message}

    except Exception as e:
        logging.error(f"Connection test failed: {str(e)}")
        return {"status": "ERROR", "message": f"Connection test error: {str(e)}"}


def create_custom_object(object_name, label, plural_label, description=""):
    """Creates a custom object using the Metadata API."""
    try:
        if not ensure_connection():
            return {"success": False, "message": "❌ Salesforce connection failed"}

        if not object_name.endswith("__c"):
            object_name += "__c"

        # Check if object already exists
        if check_object_exists(object_name):
            return {
                "success": False,
                "message": f"❌ Custom Object '{object_name}' already exists in the org.",
            }

        logging.info(
            f"Attempting to create custom object: {object_name} using Metadata API"
        )

        md_api = sf_conn.mdapi

        # Create the object metadata - only standard fields
        object_data = {
            "fullName": object_name,
            "label": label,
            "pluralLabel": plural_label,
            "nameField": {"label": f"{label} Name", "type": "Text", "length": 80},
            "deploymentStatus": md_api.DeploymentStatus("Deployed"),
            "sharingModel": md_api.SharingModel("ReadWrite"),
        }

        # Add description if provided
        if description:
            object_data["description"] = description

        # Create custom object
        custom_object = md_api.CustomObject(**object_data)
        logging.info(f"Creating basic object with mdapi: {object_name}")

        # Call the create method and handle the result properly
        try:
            result = md_api.CustomObject.create(custom_object)
            logging.info(f"Object creation result: {result}")

            # Give Salesforce time to process
            time.sleep(3)

            if check_object_exists(object_name):
                return {
                    "success": True,
                    "message": f"✅ Successfully created Custom Object '{object_name}' with standard fields.\n\n📋 **Standard Fields Created:**\n• Name ({label} Name) - Text(80)\n• Created By - Lookup(User)\n• Created Date - DateTime\n• Last Modified By - Lookup(User)\n• Last Modified Date - DateTime\n• Owner - Lookup(User,Group)\n\n💡 You can now add custom fields to this object if needed.",
                }
            else:
                return {
                    "success": False,
                    "message": f"❌ Object creation may have failed - object not found after creation",
                }

        except Exception as create_error:
            error_str = str(create_error)
            if "DUPLICATE_DEVELOPER_NAME" in error_str:
                return {
                    "success": False,
                    "message": f"❌ Custom Object '{object_name}' already exists in the org.",
                }
            else:
                return {"success": False, "message": f"❌ Failed to create object: {error_str}"}

    except Exception as e:
        error_message = f"❌ An exception occurred during object creation: {str(e)}"
        logging.error(error_message)
        traceback.print_exc(file=sys.stderr)
        return {"success": False, "message": error_message}


def create_custom_field(object_name, field_label, field_type="Text", **kwargs):
    """Creates a custom field on an object using the Metadata API - WORKING VERSION."""
    try:
        if not ensure_connection():
            return {"success": False, "message": "❌ Salesforce connection failed"}

        # Generate API name from label
        field_name_base = ''.join(c for c in field_label if c.isalnum() or c == ' ')
        field_api_name = field_name_base.replace(' ', '_') + '__c'
        full_name = f"{object_name}.{field_api_name}"

        logging.info(f"Attempting to create field '{field_api_name}' on object '{object_name}' using Metadata API")

        md_api = sf_conn.mdapi

        # Create field definition
        field_data = {
            'fullName': full_name,
            'label': field_label,
            'type': field_type
        }

        # Add type-specific properties
        if field_type == 'Text':
            field_data['length'] = kwargs.get('length', 255)
        elif field_type == 'LongTextArea':
            field_data['length'] = kwargs.get('length', 32768)
            field_data['visibleLines'] = kwargs.get('visibleLines', 3)
        elif field_type == 'Number':
            field_data['precision'] = kwargs.get('precision', 18)
            field_data['scale'] = kwargs.get('scale', 0)
        elif field_type == 'Currency':
            field_data['precision'] = kwargs.get('precision', 18)
            field_data['scale'] = kwargs.get('scale', 2)
        elif field_type == 'Percent':
            field_data['precision'] = kwargs.get('precision', 18)
            field_data['scale'] = kwargs.get('scale', 2)
        elif field_type == 'Checkbox':
            field_data['defaultValue'] = kwargs.get('defaultValue', False)
        elif field_type == 'Picklist':
            picklist_values = kwargs.get('picklist_values', kwargs.get('picklistValues', ['Option 1', 'Option 2', 'Option 3']))
            field_data['valueSet'] = {
                'valueSetDefinition': {
                    'value': [{'fullName': val, 'default': False, 'label': val} for val in picklist_values],
                    'sorted': False
                }
            }

        # Add required attribute if specified
        if kwargs.get('required', False):
            field_data['required'] = True

        # Add unique attribute if specified
        if kwargs.get('unique', False):
            field_data['unique'] = True

        custom_field = md_api.CustomField(**field_data)

        logging.info(f"Creating field with mdapi: {full_name}")

        try:
            result = md_api.CustomField.create(custom_field)
            logging.info(f"Field creation result: {result}")

            return {
                "success": True,
                "message": f"✅ Successfully created Field '{field_api_name}' on Object '{object_name}'.\n\n📋 **Field Details:**\n• Label: {field_label}\n• API Name: {field_api_name}\n• Type: {field_type}\n• Required: {kwargs.get('required', False)}"
            }

        except Exception as create_error:
            error_str = str(create_error)
            if "DUPLICATE_DEVELOPER_NAME" in error_str:
                return {"success": False, "message": f"❌ Field '{field_api_name}' already exists on object '{object_name}'."}
            else:
                return {"success": False, "message": f"❌ Failed to create field: {error_str}"}

    except Exception as e:
        error_message = f"❌ An exception occurred during field creation: {str(e)}"
        logging.error(error_message)
        traceback.print_exc(file=sys.stderr)
        return {"success": False, "message": error_message}


def update_custom_field(object_name, field_name, field_label=None, **kwargs):
    """Updates a custom field on an object using the Metadata API."""
    try:
        if not ensure_connection():
            return {"success": False, "message": "❌ Salesforce connection failed"}

        # Ensure field name has __c suffix
        if not field_name.endswith('__c'):
            field_name += '__c'

        full_name = f"{object_name}.{field_name}"
        logging.info(f"Attempting to update field '{field_name}' on object '{object_name}' using Metadata API")

        md_api = sf_conn.mdapi

        # Get current field metadata first
        try:
            current_field = md_api.CustomField.read(full_name)
            if not current_field:
                return {"success": False, "message": f"❌ Field '{field_name}' does not exist on object '{object_name}'."}
        except Exception as read_error:
            return {"success": False, "message": f"❌ Failed to read current field metadata: {str(read_error)}"}

        # Update only the provided fields
        updates_made = []

        if field_label is not None:
            current_field.label = field_label
            updates_made.append(f"Label: '{field_label}'")

        # Update type-specific properties
        if 'length' in kwargs and hasattr(current_field, 'length'):
            current_field.length = kwargs['length']
            updates_made.append(f"Length: {kwargs['length']}")

        if 'precision' in kwargs and hasattr(current_field, 'precision'):
            current_field.precision = kwargs['precision']
            updates_made.append(f"Precision: {kwargs['precision']}")

        if 'scale' in kwargs and hasattr(current_field, 'scale'):
            current_field.scale = kwargs['scale']
            updates_made.append(f"Scale: {kwargs['scale']}")

        if 'defaultValue' in kwargs and hasattr(current_field, 'defaultValue'):
            current_field.defaultValue = kwargs['defaultValue']
            updates_made.append(f"Default Value: {kwargs['defaultValue']}")

        if 'required' in kwargs:
            current_field.required = kwargs['required']
            updates_made.append(f"Required: {kwargs['required']}")

        if 'picklist_values' in kwargs and hasattr(current_field, 'valueSet'):
            picklist_values = kwargs['picklist_values']
            current_field.valueSet = {
                'valueSetDefinition': {
                    'value': [{'fullName': val, 'default': False, 'label': val} for val in picklist_values],
                    'sorted': False
                }
            }
            updates_made.append(f"Picklist Values: {picklist_values}")

        if not updates_made:
            return {"success": False, "message": "❌ No updates provided. Please specify at least one field property to update."}

        # Update the field
        try:
            result = md_api.CustomField.update(current_field)
            logging.info(f"Field update result: {result}")

            # Give Salesforce time to process
            time.sleep(2)

            return {
                "success": True,
                "message": f"✅ Successfully updated Field '{field_name}' on Object '{object_name}'.\n\n📝 **Updates Made:**\n" + "\n".join([f"• {update}" for update in updates_made])
            }

        except Exception as update_error:
            error_str = str(update_error)
            return {"success": False, "message": f"❌ Failed to update field: {error_str}"}

    except Exception as e:
        error_message = f"❌ An exception occurred during field update: {str(e)}"
        logging.error(error_message)
        traceback.print_exc(file=sys.stderr)
        return {"success": False, "message": error_message}


def delete_custom_field(object_name, field_name):
    """Deletes a custom field from an object using the Metadata API."""
    try:
        if not ensure_connection():
            return {"success": False, "message": "❌ Salesforce connection failed"}

        # Ensure field name has __c suffix
        if not field_name.endswith('__c'):
            field_name += '__c'

        full_name = f"{object_name}.{field_name}"
        logging.info(f"Attempting to delete field '{field_name}' from object '{object_name}' using Metadata API")

        md_api = sf_conn.mdapi

        # Delete the field
        try:
            result = md_api.CustomField.delete(full_name)
            logging.info(f"Field deletion result: {result}")

            # Give Salesforce time to process
            time.sleep(2)

            return {
                "success": True,
                "message": f"✅ Successfully deleted Field '{field_name}' from Object '{object_name}'.\n\n⚠️ **Note:** All data in this field has been permanently removed from all records."
            }

        except Exception as delete_error:
            error_str = str(delete_error)
            if "FIELD_NOT_FOUND" in error_str or "does not exist" in error_str:
                return {"success": False, "message": f"❌ Field '{field_name}' does not exist on object '{object_name}'."}
            else:
                return {"success": False, "message": f"❌ Failed to delete field: {error_str}"}

    except Exception as e:
        error_message = f"❌ An exception occurred during field deletion: {str(e)}"
        logging.error(error_message)
        traceback.print_exc(file=sys.stderr)
        return {"success": False, "message": error_message}


def update_custom_object(object_name, label=None, plural_label=None, description=None):
    """Updates a custom object using the Metadata API."""
    try:
        if not ensure_connection():
            return {"success": False, "message": "❌ Salesforce connection failed"}

        if not object_name.endswith('__c'):
            object_name += '__c'

        # Check if object exists
        if not check_object_exists(object_name):
            return {"success": False, "message": f"❌ Custom Object '{object_name}' does not exist in the org."}

        logging.info(f"Attempting to update custom object: {object_name} using Metadata API")

        md_api = sf_conn.mdapi

        # Get current object metadata first
        try:
            current_object = md_api.CustomObject.read(object_name)
            if not current_object:
                return {"success": False, "message": f"❌ Could not retrieve current metadata for '{object_name}'."}
        except Exception as read_error:
            return {"success": False, "message": f"❌ Failed to read current object metadata: {str(read_error)}"}

        # Update only the provided fields
        updates_made = []

        if label is not None:
            current_object.label = label
            updates_made.append(f"Label: '{label}'")
            # Also update the name field label
            if hasattr(current_object, 'nameField') and current_object.nameField:
                current_object.nameField.label = f'{label} Name'

        if plural_label is not None:
            current_object.pluralLabel = plural_label
            updates_made.append(f"Plural Label: '{plural_label}'")

        if description is not None:
            current_object.description = description
            updates_made.append(f"Description: '{description}'")

        if not updates_made:
            return {"success": False, "message": "❌ No updates provided. Please specify at least one field to update."}

        # Update the object
        try:
            result = md_api.CustomObject.update(current_object)
            logging.info(f"Object update result: {result}")

            # Give Salesforce time to process
            time.sleep(2)

            return {
                "success": True,
                "message": f"✅ Successfully updated Custom Object '{object_name}'.\n\n📝 **Updates Made:**\n" + "\n".join([f"• {update}" for update in updates_made])
            }

        except Exception as update_error:
            error_str = str(update_error)
            return {"success": False, "message": f"❌ Failed to update object: {error_str}"}

    except Exception as e:
        error_message = f"❌ An exception occurred during object update: {str(e)}"
        logging.error(error_message)
        traceback.print_exc(file=sys.stderr)
        return {"success": False, "message": error_message}


def delete_custom_object(object_name):
    """Deletes a custom object using the Metadata API."""
    try:
        if not ensure_connection():
            return {"success": False, "message": "❌ Salesforce connection failed"}

        if not object_name.endswith('__c'):
            object_name += '__c'

        # Check if object exists
        if not check_object_exists(object_name):
            return {"success": False, "message": f"❌ Custom Object '{object_name}' does not exist in the org."}

        logging.info(f"Attempting to delete custom object: {object_name} using Metadata API")

        md_api = sf_conn.mdapi

        # Delete the object
        try:
            result = md_api.CustomObject.delete(object_name)
            logging.info(f"Object deletion result: {result}")

            # Give Salesforce time to process
            time.sleep(3)

            # Verify deletion
            if not check_object_exists(object_name):
                return {
                    "success": True,
                    "message": f"✅ Successfully deleted Custom Object '{object_name}' from the org.\n\n⚠️ **Note:** All records and related data for this object have been permanently removed."
                }
            else:
                return {"success": False, "message": f"❌ Object deletion may have failed - object still exists after deletion attempt"}

        except Exception as delete_error:
            error_str = str(delete_error)
            return {"success": False, "message": f"❌ Failed to delete object: {error_str}"}

    except Exception as e:
        error_message = f"❌ An exception occurred during object deletion: {str(e)}"
        logging.error(error_message)
        traceback.print_exc(file=sys.stderr)
        return {"success": False, "message": error_message}


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

        bulk_object = getattr(sf_conn.bulk, object_name)
        results = bulk_object.insert(records)

        success_count = sum(1 for r in results if r.get("success"))
        error_count = len(records) - success_count
        errors = [r.get("error", "Unknown error") for r in results if not r.get("success")][
            :5
        ]

        return {
            "operation": "insert",
            "object_name": object_name,
            "total_records": len(records),
            "success_count": success_count,
            "error_count": error_count,
            "errors": errors,
            "message": f"Bulk insert completed. Success: {success_count}, Errors: {error_count}",
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

        if not all("Id" in record for record in records):
            return {"error": "All records must have an 'Id' field for update"}

        logging.info(f"Starting bulk update of {len(records)} records in {object_name}")

        bulk_object = getattr(sf_conn.bulk, object_name)
        results = bulk_object.update(records)

        success_count = sum(1 for r in results if r.get("success"))
        error_count = len(records) - success_count
        errors = [r.get("error", "Unknown error") for r in results if not r.get("success")][
            :5
        ]

        return {
            "operation": "update",
            "object_name": object_name,
            "total_records": len(records),
            "success_count": success_count,
            "error_count": error_count,
            "errors": errors,
            "message": f"Bulk update completed. Success: {success_count}, Errors: {error_count}",
        }

    except Exception as e:
        raise Exception(f"Bulk update failed: {str(e)}")


def describe_object(object_name):
    """Get object metadata."""
    try:
        if not ensure_connection():
            raise Exception("Cannot establish Salesforce connection")

        describe_result = getattr(sf_conn, object_name).describe()

        fields_info = [
            {
                "name": f["name"],
                "type": f["type"],
                "label": f["label"],
                "required": not f["nillable"] and not f["defaultedOnCreate"],
                "updateable": f["updateable"],
                "createable": f["createable"],
            }
            for f in describe_result["fields"]
        ]

        return {
            "object_name": object_name,
            "label": describe_result["label"],
            "total_fields": len(fields_info),
            "fields": fields_info,
            "createable": describe_result["createable"],
            "updateable": describe_result["updateable"],
            "deletable": describe_result["deletable"],
        }

    except Exception as e:
        raise Exception(f"Describe object failed: {str(e)}")


def do_list_tools(params):
    """Lists available tools."""
    return {
        "tools": [
            {
                "name": "get_object_required_fields",
                "description": "Get required fields for any Salesforce object to help with record creation. Use this when user wants to create records but doesn't specify field values.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "object_name": {
                            "type": "string",
                            "description": "Salesforce object API name (e.g., 'Account', 'Contact', 'Opportunity')",
                        }
                    },
                    "required": ["object_name"],
                },
            },
            {
                "name": "create_records_with_validation",
                "description": "Create one or multiple records for any Salesforce object with proper validation. ONLY use this when user provides specific field values. If user doesn't provide field values, use get_object_required_fields first to ask for them.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "object_name": {
                            "type": "string",
                            "description": "Salesforce object API name (e.g., 'Account', 'Contact', 'Opportunity')",
                        },
                        "records_data": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "Array of records to create. Each record must include ALL required fields with their values.",
                        },
                    },
                    "required": ["object_name", "records_data"],
                },
            },
            {
                "name": "run_apex_tests_comprehensive",
                "description": "Run Apex tests with comprehensive options and detailed failure reporting including line numbers and stack traces. Prevents duplicate executions by checking if tests are already running for the specified classes.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "class_names": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Apex test class names to run (required for RunSpecifiedTests)",
                        },
                        "test_level": {
                            "type": "string",
                            "enum": [
                                "RunSpecifiedTests",
                                "RunLocalTests",
                                "RunAllTestsInOrg",
                            ],
                            "default": "RunSpecifiedTests",
                            "description": "Test execution level: RunSpecifiedTests (specific classes), RunLocalTests (exclude managed packages), RunAllTestsInOrg (all tests)",
                        },
                        "async_execution": {
                            "type": "boolean",
                            "default": False,
                            "description": "Whether to wait for completion (false) or return job ID immediately (true)",
                        },
                        "code_coverage": {
                            "type": "boolean",
                            "default": True,
                            "description": "Whether to calculate code coverage",
                        },
                        "verbose": {
                            "type": "boolean",
                            "default": False,
                            "description": "Include detailed information about passing tests",
                        },
                    },
                },
            },
            {
                "name": "get_current_org_coverage",
                "description": "Get current code coverage for the entire org without running tests. Shows which classes have high, medium, or low coverage.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "check_test_status_and_coverage",
                "description": "Check if tests are running and get coverage when complete. Use this when user asks for coverage - it will tell them if tests are still running or show current coverage WITH DETAILED FAILURE INFORMATION including line numbers and error messages.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "class_names": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional: specific test class names to check coverage for",
                        }
                    },
                },
            },
            {
                "name": "salesforce_connection_test",
                "description": "Test connection to Salesforce",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "salesforce_query",
                "description": "Run SOQL query. Note: COUNT queries cannot use LIMIT clause.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "SOQL query. For counting records, use 'SELECT COUNT(Id) FROM ObjectName' (no LIMIT needed for COUNT queries)",
                        },
                        "limit": {"type": "integer", "default": 200},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "query_tooling_api_direct",
                "description": "Query Tooling API directly for coverage data and other tooling objects",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "SOQL query for Tooling API (e.g., coverage data, metadata objects)",
                        }
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "create_custom_object",
                "description": "Creates a new Salesforce custom object with standard fields. Use this for any request to create, make, or build an object.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "object_name": {
                            "type": "string",
                            "description": "API name with __c suffix (e.g., 'Disturb__c', 'Joker__c')",
                        },
                        "label": {
                            "type": "string",
                            "description": "Display label (e.g., 'Disturb', 'Joker')",
                        },
                        "plural_label": {
                            "type": "string",
                            "description": "Plural label (e.g., 'Disturbs', 'Jokers')",
                        },
                        "description": {
                            "type": "string",
                            "description": "Object description",
                        },
                    },
                    "required": ["object_name", "label", "plural_label"],
                },
            },
            {
                "name": "update_custom_object",
                "description": "Updates an existing custom object's properties like label, plural label, or description.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "object_name": {
                            "type": "string",
                            "description": "API name of existing object (e.g., 'Joker__c')",
                        },
                        "label": {
                            "type": "string",
                            "description": "New display label (optional)",
                        },
                        "plural_label": {
                            "type": "string",
                            "description": "New plural label (optional)",
                        },
                        "description": {
                            "type": "string",
                            "description": "New description (optional)",
                        },
                    },
                    "required": ["object_name"],
                },
            },
            {
                "name": "delete_custom_object",
                "description": "Deletes a custom object permanently. WARNING: This will delete all data!",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "object_name": {
                            "type": "string",
                            "description": "API name of object to delete (e.g., 'Joker__c')",
                        }
                    },
                    "required": ["object_name"],
                },
            },
            {
                "name": "create_custom_field",
                "description": "Creates a new custom field on an existing object.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "object_name": {
                            "type": "string",
                            "description": "Target object API name (e.g., 'Joker__c')",
                        },
                        "field_label": {
                            "type": "string",
                            "description": "Field display label (e.g., 'Fixer', 'Turn')",
                        },
                        "field_type": {
                            "type": "string",
                            "enum": [
                                "Text",
                                "Number",
                                "Currency",
                                "Date",
                                "DateTime",
                                "Checkbox",
                                "Picklist",
                                "Email",
                                "Phone",
                            ],
                            "default": "Text",
                        },
                        "length": {"type": "integer", "description": "Text field length"},
                        "precision": {
                            "type": "integer",
                            "description": "Number/Currency field precision",
                        },
                        "scale": {
                            "type": "integer",
                            "description": "Number/Currency field scale",
                        },
                        "defaultValue": {
                            "type": "boolean",
                            "description": "Checkbox default value",
                        },
                        "picklist_values": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Picklist options",
                        },
                        "required": {"type": "boolean", "description": "Is field required"},
                    },
                    "required": ["object_name", "field_label"],
                },
            },
            {
                "name": "update_custom_field",
                "description": "Updates an existing custom field's properties.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "object_name": {
                            "type": "string",
                            "description": "Target object API name (e.g., 'Joker__c')",
                        },
                        "field_name": {
                            "type": "string",
                            "description": "Field API name to update (e.g., 'Fixer__c')",
                        },
                        "field_label": {
                            "type": "string",
                            "description": "New field label (optional)",
                        },
                        "length": {
                            "type": "integer",
                            "description": "New text field length (optional)",
                        },
                        "precision": {
                            "type": "integer",
                            "description": "New number/currency precision (optional)",
                        },
                        "scale": {
                            "type": "integer",
                            "description": "New number/currency scale (optional)",
                        },
                        "defaultValue": {
                            "type": "boolean",
                            "description": "New checkbox default value (optional)",
                        },
                        "picklist_values": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "New picklist options (optional)",
                        },
                        "required": {
                            "type": "boolean",
                            "description": "Whether field is required (optional)",
                        },
                    },
                    "required": ["object_name", "field_name"],
                },
            },
            {
                "name": "delete_custom_field",
                "description": "Deletes a custom field permanently. WARNING: This will delete all field data!",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "object_name": {
                            "type": "string",
                            "description": "Target object API name (e.g., 'Joker__c')",
                        },
                        "field_name": {
                            "type": "string",
                            "description": "Field API name to delete (e.g., 'Fixer__c')",
                        },
                    },
                    "required": ["object_name", "field_name"],
                },
            },
            {
                "name": "salesforce_bulk_insert_simple",
                "description": "Bulk insert records",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "object_name": {"type": "string"},
                        "csv_data": {"type": "string"},
                    },
                    "required": ["object_name", "csv_data"],
                },
            },
            {
                "name": "salesforce_bulk_update_simple",
                "description": "Bulk update records",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "object_name": {"type": "string"},
                        "csv_data": {"type": "string"},
                    },
                    "required": ["object_name", "csv_data"],
                },
            },
            {
                "name": "salesforce_describe_object",
                "description": "Get object metadata",
                "inputSchema": {
                    "type": "object",
                    "properties": {"object_name": {"type": "string"}},
                    "required": ["object_name"],
                },
            },
        ],
    }


def do_call_tool(params):
    """Handle tool execution."""
    tool_name = params.get("name")
    arguments = params.get("arguments", {})

    logging.info(f"=== Executing tool: {tool_name} ===")
    logging.info(f"Arguments: {json.dumps(arguments, indent=2)}")

    try:
        if tool_name == "get_object_required_fields":
            result = get_object_required_fields(**arguments)
            if not result["success"]:
                return {"content": [{"type": "text", "text": result["message"]}]}

            required_fields = result["required_fields"]
            object_name = result["object_name"]
            object_label = result["object_label"]

            if not required_fields:
                message = f"📋 **{object_label} ({object_name})** has no required fields for record creation.\n\nYou can create records with any optional fields you want to populate.\n\n💡 **To create records, please provide the field values you want to set.**"
            else:
                field_details = []
                for field in required_fields:
                    field_info = (
                        f"• **{field['label']}** ({field['name']}) - {field['type']}"
                    )
                    if field["type"] == "string" and field.get("length"):
                        field_info += f" (max {field['length']} characters)"
                    elif field["type"] == "picklist" and field.get("picklistValues"):
                        field_info += (
                            f"\n  Valid options: {', '.join(field['picklistValues'])}"
                        )
                    elif field.get("format"):
                        field_info += f"\n  Format: {field['format']}"

                    field_details.append(field_info)

                message = (
                    f"📋 **Required Fields for {object_label} ({object_name}):**\n\n"
                    + "\n".join(field_details)
                    + "\n\n💡 **Please provide values for ALL these required fields to create the record.**"
                )

            return {"content": [{"type": "text", "text": message}]}

        elif tool_name == "create_records_with_validation":
            result = create_records_with_validation(**arguments)
            if not result["success"]:
                return {"content": [{"type": "text", "text": result["message"]}]}

            message = result["message"]
            if result.get("record_ids"):
                message += (
                    "\n\n📋 **Created Record IDs:**\n"
                    + "\n".join(
                        [f"• {record_id}" for record_id in result["record_ids"][:10]]
                    )
                )
                if len(result["record_ids"]) > 10:
                    message += f"\n... and {len(result['record_ids']) - 10} more records"

            if result.get("errors"):
                message += (
                    "\n\n❌ **Errors:**\n"
                    + "\n".join([f"• {error}" for error in result["errors"]])
                )

            return {"content": [{"type": "text", "text": message}]}

        elif tool_name == "run_apex_tests_comprehensive":
            result = run_apex_tests_comprehensive(**arguments)
            return {"content": [{"type": "text", "text": result["message"]}]}

        elif tool_name == "get_current_org_coverage":
            result = get_current_org_coverage()
            return {"content": [{"type": "text", "text": result["message"]}]}

        elif tool_name == "check_test_status_and_coverage":
            result = check_test_status_and_coverage(**arguments)
            return {"content": [{"type": "text", "text": result["message"]}]}

        elif tool_name == "salesforce_connection_test":
            result = test_connection()
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"🔗 Connection Test Result:\n{result['message']}",
                    }
                ]
            }

        elif tool_name == "salesforce_query":
            if not ensure_connection():
                return {
                    "content": [
                        {"type": "text", "text": "❌ Cannot connect to Salesforce org"}
                    ]
                }

            query = arguments.get("query")
            # Normalize the query (fix COUNT() to COUNT(Id) and remove LIMIT from COUNT queries)
            query = normalize_soql_query(query)

            if "$SALESFORCE_USERNAME" in query:
                logging.info(
                    "Found $SALESFORCE_USERNAME placeholder. Replacing with environment variable."
                )
                username = os.getenv("SALESFORCE_USERNAME")
                if not username:
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": "❌ Error: $SALESFORCE_USERNAME placeholder used, but SALESFORCE_USERNAME environment variable is not set.",
                            }
                        ]
                    }
                query = query.replace("$SALESFORCE_USERNAME", f"'{username}'")

            # Only add LIMIT if it's not a COUNT query
            limit = arguments.get("limit", 200)
            if "LIMIT" not in query.upper() and "COUNT(" not in query.upper():
                query += f" LIMIT {min(limit, 2000)}"

            logging.info(f"Executing normalized query: {query}")
            result = sf_conn.query_all(query)

            return {
                "content": [
                    {
                        "type": "text",
                        "text": "📊 SOQL Query Results:\n\n"
                        + f"Query: {query}\n"
                        + f"Records Returned: {len(result['records'])}\n"
                        + f"Total Size: {result['totalSize']}\n\n"
                        + f"Data:\n{json.dumps(result['records'], indent=2)}",
                    }
                ]
            }

        elif tool_name == "query_tooling_api_direct":
            query = arguments.get("query")
            result = query_tooling_api(query)
            if result:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"🔧 **Tooling API Query Results:**\n\n"
                            + f"Query: {query}\n"
                            + f"Records Returned: {len(result.get('records', []))}\n"
                            + f"Total Size: {result.get('totalSize', 0)}\n\n"
                            + f"Data:\n{json.dumps(result.get('records', []), indent=2)}",
                        }
                    ]
                }
            else:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": "❌ Tooling API query failed. Check logs for details.",
                        }
                    ]
                }

        elif tool_name == "create_custom_object":
            result = create_custom_object(**arguments)
            return {"content": [{"type": "text", "text": result["message"]}]}

        elif tool_name == "update_custom_object":
            result = update_custom_object(**arguments)
            return {"content": [{"type": "text", "text": result["message"]}]}

        elif tool_name == "delete_custom_object":
            result = delete_custom_object(**arguments)
            return {"content": [{"type": "text", "text": result["message"]}]}

        elif tool_name == "create_custom_field":
            result = create_custom_field(**arguments)
            return {"content": [{"type": "text", "text": result["message"]}]}

        elif tool_name == "update_custom_field":
            result = update_custom_field(**arguments)
            return {"content": [{"type": "text", "text": result["message"]}]}

        elif tool_name == "delete_custom_field":
            result = delete_custom_field(**arguments)
            return {"content": [{"type": "text", "text": result["message"]}]}

        elif tool_name == "salesforce_bulk_insert_simple":
            result = execute_bulk_insert_simple(**arguments)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"📥 Bulk Insert Results:\n\n"
                        + f"Message: {result.get('message', 'N/A')}"
                        + (
                            f"\nErrors:\n"
                            + "\n".join([f" • {e}" for e in result.get("errors", [])])
                            if result.get("errors")
                            else ""
                        ),
                    }
                ]
            }

        elif tool_name == "salesforce_bulk_update_simple":
            result = execute_bulk_update_simple(**arguments)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"📤 Bulk Update Results:\n\n"
                        + f"Message: {result.get('message', 'N/A')}"
                        + (
                            f"\nErrors:\n"
                            + "\n".join([f" • {e}" for e in result.get("errors", [])])
                            if result.get("errors")
                            else ""
                        ),
                    }
                ]
            }

        elif tool_name == "salesforce_describe_object":
            result = describe_object(**arguments)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"📋 Object Metadata: {result['object_name']}\n\n"
                        + f"Label: {result['label']}\n"
                        + f"Total Fields: {result['total_fields']}\n\n"
                        + f"Fields (first 10):\n{json.dumps(result['fields'][:10], indent=2)}",
                    }
                ]
            }

        else:
            return {"content": [{"type": "text", "text": f"❌ Unknown tool: {tool_name}"}]}

    except Exception as e:
        logging.error(f"Tool execution error: {str(e)}")
        traceback.print_exc(file=sys.stderr)
        return {
            "content": [
                {"type": "text", "text": f"❌ Error executing {tool_name}: {str(e)}"}
            ]
        }


def do_initialize(params):
    """Initialize the MCP server."""
    logging.info("=== Salesforce MCP Server initializing ===")
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "salesforce-mcp-server", "version": "21.0.0"},
    }


def do_initialized(params):
    """Handle initialized notification."""
    logging.info("=== Salesforce MCP Server ready! ===")
    return None


def main():
    """Main MCP server loop."""
    try:
        logging.info("=== Salesforce MCP Server Starting ===")

        RPC_METHODS = {
            "initialize": do_initialize,
            "initialized": do_initialized,
            "tools/list": do_list_tools,
            "tools/call": do_call_tool,
        }

        logging.info("🚀 MCP Server ready...")

        for line_num, line in enumerate(sys.stdin, 1):
            try:
                line = line.strip()
                if not line:
                    continue

                request = json.loads(line)
                request_id = request.get("id")
                method_name = request.get("method")
                params = request.get("params", {})

                if method_name in RPC_METHODS:
                    try:
                        result = RPC_METHODS[method_name](params)
                        if request_id is not None:
                            response = {
                                "jsonrpc": "2.0",
                                "id": request_id,
                                "result": result,
                            }
                            print(json.dumps(response), flush=True)

                    except Exception as e:
                        if request_id is not None:
                            response = {
                                "jsonrpc": "2.0",
                                "id": request_id,
                                "error": {"code": -32603, "message": str(e)},
                            }
                            print(json.dumps(response), flush=True)
                else:
                    logging.warning(f"Unknown method: {method_name}")

            except Exception as e:
                logging.error(f"Error on line {line_num}: {e}")

    except Exception as e:
        logging.critical(f"Fatal error in main loop: {e}")
        traceback.print_exc(file=sys.stderr)


if __name__ == "__main__":
    main()
