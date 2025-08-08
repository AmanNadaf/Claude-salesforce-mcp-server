# Salesforce MCP Server with Enhanced Test Execution

A production-ready Salesforce MCP (Model Context Protocol) server featuring comprehensive test execution with detailed failure reporting, intelligent authentication, and complete Salesforce API coverage for universal LLM integration.

## ✨ Key Features

### 🧪 **Enhanced Test Execution & Coverage**
- **Detailed Failure Reporting** - Line numbers, stack traces, and error context
- **Comprehensive Coverage Analysis** - Org-wide and class-specific coverage metrics  
- **Smart Test Monitoring** - Automatic status detection and result retrieval
- **Multiple Execution Methods** - Tooling API, REST API, and queue-based approaches

### 🔐 **Intelligent Authentication**
- **Simple-Salesforce Backend** - Reliable, battle-tested Salesforce client
- **Flexible Credential Management** - Environment variables with secure token handling
- **Connection Retry Logic** - Automatic reconnection with exponential backoff
- **Session Management** - Persistent connections with validation

### 📊 **Comprehensive Salesforce API Coverage**
- **SOQL/SOSL Queries** - Full query capabilities with smart normalization
- **DML Operations** - Create, Read, Update, Delete with validation
- **Metadata Management** - Custom objects and fields creation/modification
- **Bulk Operations** - High-performance CSV-based bulk processing
- **Test Execution** - Complete Apex test lifecycle management

## 🚀 Quick Start

### 1. Install Dependencies
```bash
pip3 install simple-salesforce python-dotenv requests
```

### 2. Configure Authentication
Create a `.env` file with your Salesforce configuration:

```env
SALESFORCE_USERNAME=your_username@company.com
SALESFORCE_PASSWORD=your_password
SALESFORCE_SECURITY_TOKEN=your_security_token
SALESFORCE_INSTANCE_URL=https://your-domain.my.salesforce.com  # Optional
```

### 3. Run the Server
```bash
# Start MCP server
python3 salesforce_mcp_server.py

# Or run as module
python3 -m salesforce_mcp_server
```

### 4. Test the Setup
Test basic connectivity:
```python
# Test connection
from salesforce_mcp_server import test_connection
result = test_connection()
print(result['message'])
```

## 🧪 Enhanced Test Execution Features

### **Detailed Failure Reporting**
When tests fail, get comprehensive diagnostic information:

```
🧪 **Apex Test Execution Complete**

📊 **Summary:**
   • Total Tests: 2
   • ✅ Passed: 0  
   • ❌ Failed: 2
   • Success Rate: 0.0%
   • Job ID: 707gL00000BEzFbQAL

❌ **Failed Tests (Detailed):**

**1. ContactManagerTest.testSyncMailingAddress_NoAccountFail**
   📍 **Line:** 17
   ⏱️ **Runtime:** 150ms
   🚨 **Error:** System.DmlException: Insert failed. FIELD_INTEGRITY_EXCEPTION, 
        There's a problem with this country: [BillingCountry]
   📋 **Stack Trace:**
      Class.ContactManagerTest.makeData: line 17, column 1

**2. ContactManagerTest.testSyncMailingAddress_Success**
   📍 **Line:** 17
   🚨 **Error:** System.DmlException: Insert failed. FIELD_INTEGRITY_EXCEPTION,
        There's a problem with this country: [BillingCountry]
   📋 **Stack Trace:**
      Class.ContactManagerTest.makeData: line 17, column 1

📈 **Code Coverage Details:**
   🟢 AccountManager: 100.0% (7/7 lines)
   🔴 ContactManager: 0.0% (0/14 lines)

💡 **Recommendations:**
   • Review failed test details above
   • Check the specific line numbers where failures occurred
   • Fix the underlying code issues causing test failures
   • Re-run tests after making corrections
```

### **Smart Coverage Integration**
Check coverage with automatic failure detection:

```python
# After running tests, check coverage to see detailed results
check_test_status_and_coverage(class_names=['ContactManagerTest'])
```

## 🛠️ Available Tools (15 Total)

### **Test Execution Tools (3)**
- `run_apex_tests_comprehensive` - Run tests with detailed failure reporting
- `check_test_status_and_coverage` - Get coverage with failure details
- `get_current_org_coverage` - Organization-wide coverage analysis

### **Data Query Tools (3)**
- `salesforce_query` - Execute SOQL queries with smart normalization
- `query_tooling_api_direct` - Direct Tooling API access for metadata
- `salesforce_connection_test` - Test and validate connection

### **Data Manipulation Tools (2)**
- `get_object_required_fields` - Get required fields for validation
- `create_records_with_validation` - Create records with comprehensive validation

### **Metadata Management Tools (6)**
- `create_custom_object` - Create custom objects with standard fields
- `create_custom_field` - Add custom fields to objects
- `update_custom_object` - Modify object properties
- `update_custom_field` - Modify field properties  
- `delete_custom_object` - Remove custom objects (with warning)
- `delete_custom_field` - Remove custom fields (with warning)

### **Bulk Operations Tools (2)**
- `salesforce_bulk_insert_simple` - Bulk insert from CSV data
- `salesforce_bulk_update_simple` - Bulk update from CSV data
- `salesforce_describe_object` - Get detailed object metadata

## 📋 Comprehensive Test Execution Example

### 1. **Run Tests with Enhanced Reporting**
```python
# Run specific test classes
run_apex_tests_comprehensive(
    class_names=['ContactManagerTest', 'AccountManagerTest'],
    test_level='RunSpecifiedTests',
    code_coverage=True,
    verbose=True
)
```

**Output:**
```
✅ Successfully enqueued 2 test classes!

📋 **Enqueued Tests:**
• ContactManagerTest (Queue ID: 709gL000003EcbdQAC)
• AccountManagerTest (Queue ID: 709gL000003EcbeQAC)

💡 **Monitor Progress:** Check Setup → Apex Test Execution
⏳ **Note:** Tests are now running. Use 'check coverage' to see results once complete.
```

### 2. **Check Results with Detailed Failures**
```python
# Get comprehensive results including failures and coverage
check_test_status_and_coverage(class_names=['ContactManagerTest'])
```

**Output includes:**
- ✅ **Test execution summary** with pass/fail counts
- 🚨 **Detailed failure analysis** with line numbers and stack traces  
- 📈 **Code coverage metrics** with class-by-class breakdown
- 💡 **Actionable recommendations** for fixing issues

### 3. **Organization-Wide Coverage Analysis**
```python
# Get org-wide coverage without running tests
get_current_org_coverage()
```

**Output:**
```
📈 **Current Org Code Coverage**

📊 **Overall Statistics:**
   • Total Classes: 127
   • Overall Coverage: 82.3%
   • Total Lines: 15,847
   • Covered Lines: 13,042
   • Uncovered Lines: 2,805

🟢 **High Coverage (≥85%):** 89 classes
   🟢 AccountManager: 100.0% (156/156 lines)
   🟢 ContactProcessor: 94.2% (243/258 lines)

🔴 **Low Coverage (<75%):** 12 classes  
   🔴 ContactManager: 0.0% (0/14 lines)
   🔴 DataHelper: 45.2% (67/148 lines)
```

## 🔧 Smart Features

### **Query Normalization**
Automatically fixes common SOQL issues:
- ✅ `COUNT()` → `COUNT(Id)` for better compatibility
- ✅ Removes `LIMIT` from `COUNT` queries (not supported)
- ✅ Handles environment variable substitution

### **Intelligent Error Handling** 
- 🔄 **Connection retry** with exponential backoff
- 📝 **Detailed error messages** with context and suggestions
- 🛡️ **Safe credential logging** (masked sensitive data)
- ⚡ **Graceful fallbacks** across multiple API approaches

### **Comprehensive Validation**
Before creating records, validates:
- ✅ **Required fields** are provided
- ✅ **Data types** match field definitions  
- ✅ **Picklist values** are valid options
- ✅ **Field lengths** don't exceed limits
- ✅ **Format requirements** (dates, emails, etc.)

## 🏗️ Architecture

### **Clean Modular Design**
```
salesforce_mcp_server.py
├── 🔐 Authentication & Connection Management
│   ├── initialize_salesforce()       # Smart connection with retry
│   ├── ensure_connection()           # Connection validation  
│   └── test_connection()             # Connection diagnostics
│
├── 🧪 Enhanced Test Execution Engine  
│   ├── run_apex_tests_comprehensive() # Multi-method test execution
│   ├── check_test_status_and_coverage() # Results with failure details
│   ├── get_test_results_for_classes() # Detailed result retrieval
│   └── parse_line_number_from_stack_trace() # Error analysis
│
├── 📊 Coverage Analysis System
│   ├── get_comprehensive_coverage_data() # Multi-source coverage
│   ├── get_current_org_coverage() # Org-wide analysis
│   └── format_comprehensive_test_results() # Rich formatting
│
├── 📝 Data Management Tools
│   ├── salesforce_query() # Smart SOQL execution
│   ├── create_records_with_validation() # Validated record creation
│   ├── get_object_required_fields() # Field requirement analysis
│   └── bulk operations # CSV-based bulk processing
│
├── 🏗️ Metadata Management
│   ├── create_custom_object() # Object creation with standards
│   ├── create_custom_field() # Field creation with validation
│   └── describe_object() # Detailed metadata retrieval
│
└── 🔧 MCP Protocol Integration
    ├── do_list_tools() # Tool discovery and registration
    ├── do_call_tool() # Tool execution with error handling
    └── main() # MCP server event loop
```

### **Multi-Method Test Execution**
Intelligent fallback system for maximum compatibility:

1. **🚀 ApexTestQueueItem** (Primary) - Direct queue-based execution
2. **🔧 Tooling API Enhanced** - Advanced API with proper payloads  
3. **📊 Analysis Mode** - Information gathering when execution unavailable

### **Comprehensive Coverage Retrieval**
Multiple data sources ensure complete coverage information:

1. **🎯 Job-Specific Coverage** - Tooling API with AsyncApexJobId
2. **📈 Aggregate Coverage** - ApexCodeCoverageAggregate queries
3. **🔍 Detailed Coverage** - ApexCodeCoverage with method-level data
4. **⏰ Recent Coverage** - Time-based coverage queries

## 📊 Configuration Options

### **Environment Variables**
| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `SALESFORCE_USERNAME` | Salesforce username | ✅ | - |
| `SALESFORCE_PASSWORD` | Salesforce password | ✅ | - |
| `SALESFORCE_SECURITY_TOKEN` | Salesforce security token | ✅ | - |
| `SALESFORCE_INSTANCE_URL` | Custom instance URL | ❌ | Auto-detected |

### **Test Execution Options**
```python
run_apex_tests_comprehensive(
    class_names=['TestClass1', 'TestClass2'],  # Specific classes
    test_level='RunSpecifiedTests',            # Test scope
    async_execution=False,                     # Wait for completion
    code_coverage=True,                        # Include coverage
    verbose=True                               # Detailed output
)
```

### **Coverage Analysis Options**
```python
check_test_status_and_coverage(
    class_names=['MyTestClass']  # Optional: specific classes
)

get_current_org_coverage()  # Org-wide analysis
```

## 🧪 Testing & Validation

### **Connection Testing**
```bash
# Test basic connectivity
python3 -c "
from salesforce_mcp_server import test_connection
result = test_connection()
print(f'Status: {result[\"status\"]}')
print(f'Message: {result[\"message\"]}')
"
```

### **Query Testing**
```bash
# Test SOQL execution
python3 -c "
from salesforce_mcp_server import do_call_tool
result = do_call_tool({
    'name': 'salesforce_query',
    'arguments': {'query': 'SELECT COUNT(Id) FROM Account'}
})
print(result)
"
```

### **Test Execution Validation**
```bash
# Run and check a test class
python3 -c "
from salesforce_mcp_server import run_apex_tests_comprehensive, check_test_status_and_coverage
import time

# Run tests
result = run_apex_tests_comprehensive(class_names=['MyTestClass'])
print('Test Execution:', result['message'])

# Wait and check results  
time.sleep(30)
coverage = check_test_status_and_coverage(class_names=['MyTestClass'])
print('Coverage Results:', coverage['message'])
"
```

## 🚀 MCP Client Integration

### **Claude Desktop Configuration**
Add to your MCP settings file:

```json
{
  "mcpServers": {
    "salesforce": {
      "command": "python3",
      "args": ["salesforce_mcp_server.py"],
      "cwd": "/path/to/your/salesforce-mcp-server",
      "env": {
        "SALESFORCE_USERNAME": "your_username@company.com",
        "SALESFORCE_PASSWORD": "your_password",
        "SALESFORCE_SECURITY_TOKEN": "your_token"
      }
    }
  }
}
```

### **Usage in Claude Desktop**
```
"Run tests for ContactManagerTest and show me detailed failure information"

"Check the code coverage for my org and tell me which classes need improvement"

"Create a new custom object called ProjectTracker with fields for Name, Status, and Due Date"

"Query all Accounts created this month and show me the first 10 results"
```

## 🔐 Security Features

### **Credential Protection**
- ✅ **Masked logging** - Sensitive data never appears in logs
- ✅ **Environment isolation** - Credentials stored in `.env` files
- ✅ **Connection validation** - Immediate feedback on auth issues
- ✅ **Session management** - Automatic token refresh and retry

### **Error Handling**
- 🛡️ **Safe error messages** - No credential exposure in errors
- 📝 **Detailed diagnostics** - Rich context without sensitive data
- 🔄 **Automatic retry** - Intelligent reconnection on auth failures
- ⚡ **Graceful degradation** - Fallbacks when features unavailable

## 📈 Performance Optimizations

### **Smart Connection Management**
- 🔄 **Connection reuse** - Persistent sessions across operations
- ⚡ **Lazy initialization** - Connect only when needed
- 🧠 **Connection validation** - Quick health checks before operations
- 🔁 **Automatic retry** - Intelligent reconnection strategies

### **Efficient Test Execution**
- 🎯 **Targeted queries** - Job-specific result retrieval
- 📊 **Parallel coverage** - Multiple coverage data sources
- 🏃 **Queue optimization** - Efficient ApexTestQueueItem usage
- 💾 **Result caching** - Avoid redundant API calls

### **Optimized Bulk Operations**
- 📦 **Batch processing** - Configurable batch sizes
- 🔄 **CSV streaming** - Memory-efficient data processing  
- ⚡ **Bulk API usage** - High-performance simple-salesforce bulk
- 📈 **Progress tracking** - Real-time operation monitoring

## 🆘 Troubleshooting

### **Common Connection Issues**
```bash
# Test connection with detailed logging
PYTHONPATH=. python3 -c "
import logging
logging.basicConfig(level=logging.DEBUG)
from salesforce_mcp_server import test_connection
result = test_connection()
print(result)
"
```

### **Test Execution Problems**
1. **Tests not found** - Verify class names are exact matches
2. **Permission errors** - Ensure user has test execution permissions
3. **Timeout issues** - Check org performance and test complexity
4. **Coverage missing** - Verify code coverage is enabled in org

### **Authentication Troubleshooting**
1. **Invalid credentials** - Check username, password, and token
2. **Security token issues** - Reset token if IP restrictions changed
3. **Instance URL problems** - Verify custom domain configuration
4. **API version compatibility** - Update to latest supported version

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Add comprehensive tests for new functionality
4. Ensure all tests pass and coverage is maintained
5. Update documentation with examples
6. Submit a pull request with detailed description

### **Development Setup**
```bash
# Clone and setup
git clone https://github.com/your-username/salesforce-mcp-server
cd salesforce-mcp-server

# Install dependencies  
pip3 install -r requirements.txt

# Setup environment
cp .env.example .env
# Edit .env with your credentials

# Run tests
python3 -m pytest tests/ -v

# Test MCP integration
python3 salesforce_mcp_server.py
```

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🆕 Recent Updates

### **Latest Features (Production Ready)**

#### 🧪 **Enhanced Test Execution Engine**
- **Line Number Extraction** - Precise error location from stack traces
- **Detailed Error Context** - Full error messages with truncation handling  
- **Smart Job Detection** - Automatic recent job ID discovery
- **Multi-Method Execution** - Fallback strategies for maximum compatibility

#### 📊 **Advanced Coverage Analysis**
- **Comprehensive Data Sources** - Tooling API + Regular API coverage
- **Class-Level Breakdown** - Individual class coverage with insights
- **Method-Level Details** - Test method coverage when available
- **Org-Wide Analytics** - Complete organization coverage analysis

#### 🔧 **Improved Tool Architecture**
- **Enhanced Error Handling** - Better error messages with context
- **Smart Query Normalization** - Automatic SOQL fixes and improvements
- **Comprehensive Validation** - Pre-creation field validation with helpful guidance
- **Flexible Authentication** - Multiple connection strategies with retry logic

#### 🚀 **Performance & Reliability**
- **Connection Optimization** - Smart connection reuse and validation
- **Robust Error Recovery** - Automatic retry with exponential backoff
- **Memory Efficiency** - Optimized bulk operations and data processing
- **Production Hardening** - Comprehensive logging and monitoring

---

**⭐ Star this repository if it helps you integrate Salesforce with your LLM workflows!**


# Claude-salesforce-mcp-server
Custom Python MCP server bridges Claude AI with Salesforce APIs, enabling natural language queries, automated data management, and intelligent sales insights. Claude processes SOQL queries conversationally while the server handles bulk operations, custom objects, and real-time sync. Delivers 75% faster data processing and AI-powered CRM automation.
<img width="1722" height="1071" alt="Screenshot 2025-08-05 at 12 43 22 AM" src="https://github.com/user-attachments/assets/e80e3c1e-1032-4bb3-9d8d-9c9d3de5ef2a" />
<img width="1166" height="884" alt="Screenshot 2025-08-05 at 12 43 31 AM" src="https://github.com/user-attachments/assets/ff0f0ccb-58fe-44a5-8217-ff3415f970f4" />

