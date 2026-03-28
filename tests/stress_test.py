#!/usr/bin/env python3
"""Stress test: deploy 100 failing pods, verify Klarsicht investigates each one."""

import json
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass

NAMESPACE = "demo"
KLARSICHT_API = "http://klarsicht-agent.klarsicht.svc:8000"
# Use kubectl port-forward or direct access
LOCAL_API = "http://localhost:8000"


@dataclass
class TestCase:
    name: str
    level: int  # 1-10 severity
    image: str
    command: list[str]
    description: str


# fmt: off
TESTS: list[TestCase] = [
    # ── Level 1: Missing environment variables (10) ──────────────────
    TestCase("missing-db-url", 1, "python:3.13-slim", ["python3", "-c",
        "import os, sys, time\ntime.sleep(2)\ndb = os.environ['DATABASE_URL']\n"],
        "Python KeyError on missing DATABASE_URL"),
    TestCase("missing-api-key", 1, "python:3.13-slim", ["python3", "-c",
        "import os, sys, time\ntime.sleep(2)\nkey = os.environ['API_KEY']\nprint('Connecting to API...')\n"],
        "Python KeyError on missing API_KEY"),
    TestCase("missing-secret", 1, "node:22-alpine", ["node", "-e",
        "setTimeout(()=>{if(!process.env.JWT_SECRET){console.error('FATAL: JWT_SECRET environment variable is not set');process.exit(1)}},2000)"],
        "Node.js missing JWT_SECRET"),
    TestCase("missing-redis-url", 1, "python:3.13-slim", ["python3", "-c",
        "import os, sys, time\ntime.sleep(2)\nprint('Starting worker...')\nredis_url = os.environ['REDIS_URL']\n"],
        "Python missing REDIS_URL"),
    TestCase("missing-smtp-host", 1, "node:22-alpine", ["node", "-e",
        "setTimeout(()=>{const h=process.env.SMTP_HOST;if(!h){console.error('Error: SMTP_HOST not configured. Email service cannot start.');process.exit(1)}},2000)"],
        "Node.js missing SMTP_HOST"),
    TestCase("missing-s3-bucket", 1, "python:3.13-slim", ["python3", "-c",
        "import os, sys, time\ntime.sleep(2)\nbucket = os.environ['S3_BUCKET']\nprint(f'Uploading to {bucket}')\n"],
        "Python missing S3_BUCKET"),
    TestCase("missing-kafka-broker", 1, "python:3.13-slim", ["python3", "-c",
        "import os, sys, time\ntime.sleep(2)\nbroker = os.environ['KAFKA_BROKERS']\nprint(f'Connecting to Kafka at {broker}')\n"],
        "Python missing KAFKA_BROKERS"),
    TestCase("missing-mongo-uri", 1, "node:22-alpine", ["node", "-e",
        "setTimeout(()=>{if(!process.env.MONGO_URI){console.error('MongoError: MONGO_URI is required');process.exit(1)}},2000)"],
        "Node.js missing MONGO_URI"),
    TestCase("wrong-db-password", 1, "python:3.13-slim", ["python3", "-c",
        "import time\ntime.sleep(2)\nprint('Connecting to PostgreSQL...')\nraise ConnectionRefusedError('FATAL: password authentication failed for user \"appuser\"')\n"],
        "Python wrong DB password"),
    TestCase("empty-config-file", 1, "python:3.13-slim", ["python3", "-c",
        "import json, time\ntime.sleep(2)\nprint('Loading config...')\nwith open('/etc/app/config.json') as f:\n    json.load(f)\n"],
        "Python FileNotFoundError on missing config"),

    # ── Level 2: Connection failures (10) ────────────────────────────
    TestCase("api-conn-refused", 2, "python:3.13-slim", ["python3", "-c",
        "import urllib.request, time\ntime.sleep(2)\nprint('Connecting to auth service...')\nurllib.request.urlopen('http://auth-service:8080/health', timeout=5)\n"],
        "Python connection refused to auth-service"),
    TestCase("dns-resolve-fail", 2, "python:3.13-slim", ["python3", "-c",
        "import socket, time\ntime.sleep(2)\nprint('Resolving payment-gateway.internal...')\nsocket.getaddrinfo('payment-gateway.internal', 443)\n"],
        "Python DNS resolution failure"),
    TestCase("http-timeout", 2, "python:3.13-slim", ["python3", "-c",
        "import urllib.request, time\ntime.sleep(2)\nprint('Calling inventory API...')\nurllib.request.urlopen('http://10.0.0.1:9999/api/v1/items', timeout=3)\n"],
        "Python HTTP timeout to non-existent service"),
    TestCase("grpc-unavailable", 2, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Connecting to gRPC server at order-service:50051...')\nprint('ERROR: grpc._channel._InactiveRpcError: <_InactiveRpcError of RPC that terminated with:')\nprint('  status = StatusCode.UNAVAILABLE')\nprint('  details = \"failed to connect to all addresses\"')\nsys.exit(1)\n"],
        "gRPC connection unavailable"),
    TestCase("redis-conn-fail", 2, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Initializing cache layer...')\nprint('redis.exceptions.ConnectionError: Error 111 connecting to redis-master:6379. Connection refused.')\nsys.exit(1)\n"],
        "Redis connection refused"),
    TestCase("elasticsearch-down", 2, "node:22-alpine", ["node", "-e",
        "setTimeout(()=>{console.log('Connecting to Elasticsearch cluster...');console.error('ConnectionError: connect ECONNREFUSED 10.43.50.100:9200');process.exit(1)},2000)"],
        "Node.js Elasticsearch connection refused"),
    TestCase("rabbitmq-refused", 2, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Connecting to RabbitMQ at amqp://rabbitmq:5672...')\nprint('pika.exceptions.AMQPConnectionError: Connection to rabbitmq:5672 failed: [Errno 111] Connection refused')\nsys.exit(1)\n"],
        "RabbitMQ connection refused"),
    TestCase("mysql-gone-away", 2, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Executing query on reporting DB...')\nprint('mysql.connector.errors.OperationalError: 2006 (HY000): MySQL server has gone away')\nsys.exit(1)\n"],
        "MySQL server gone away"),
    TestCase("ssl-cert-expired", 2, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Connecting to https://api.partner.com/v2/webhook...')\nprint('ssl.SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate has expired (_ssl.c:1000)')\nsys.exit(1)\n"],
        "SSL certificate expired"),
    TestCase("vault-sealed", 2, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Fetching secrets from Vault at https://vault.internal:8200...')\nprint('hvac.exceptions.VaultError: Vault is sealed')\nsys.exit(1)\n"],
        "HashiCorp Vault is sealed"),

    # ── Level 3: Authentication & Authorization (10) ─────────────────
    TestCase("oauth-token-expired", 3, "node:22-alpine", ["node", "-e",
        "setTimeout(()=>{console.log('Refreshing OAuth2 token...');console.error('Error: TokenExpiredError: jwt expired at 2026-03-19T23:59:59.000Z');process.exit(1)},2000)"],
        "Node.js OAuth token expired"),
    TestCase("aws-creds-invalid", 3, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Initializing AWS S3 client...')\nprint('botocore.exceptions.ClientError: An error occurred (InvalidAccessKeyId) when calling the ListBuckets operation: The AWS Access Key Id you provided does not exist in our records.')\nsys.exit(1)\n"],
        "AWS invalid credentials"),
    TestCase("k8s-sa-forbidden", 3, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Listing pods in namespace production...')\nprint('kubernetes.client.exceptions.ApiException: (403) Reason: Forbidden')\nprint('User \"system:serviceaccount:demo:default\" cannot list resource \"pods\" in API group \"\" in the namespace \"production\"')\nsys.exit(1)\n"],
        "K8s ServiceAccount forbidden"),
    TestCase("ldap-bind-fail", 3, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Binding to LDAP server ldaps://ldap.corp.local:636...')\nprint('ldap3.core.exceptions.LDAPBindError: automatic bind not successful - invalidCredentials')\nsys.exit(1)\n"],
        "LDAP bind failure"),
    TestCase("api-key-revoked", 3, "node:22-alpine", ["node", "-e",
        "setTimeout(()=>{console.log('Calling Stripe API...');console.error('StripeAuthenticationError: Invalid API Key provided: sk_live_****REDACTED');process.exit(1)},2000)"],
        "Stripe API key revoked"),
    TestCase("cert-mismatch", 3, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Establishing mTLS connection to gateway...')\nprint('ssl.SSLError: [SSL: TLSV1_ALERT_UNKNOWN_CA] tlsv1 alert unknown ca (_ssl.c:1000)')\nsys.exit(1)\n"],
        "mTLS certificate authority mismatch"),
    TestCase("firebase-denied", 3, "node:22-alpine", ["node", "-e",
        "setTimeout(()=>{console.log('Initializing Firebase Admin SDK...');console.error('Error: Credential implementation provided to initializeApp() via the \"credential\" property failed to fetch a valid Google OAuth2 access token with the following error: \"Error fetching access token: invalid_grant\"');process.exit(1)},2000)"],
        "Firebase credential error"),
    TestCase("github-rate-limit", 3, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Fetching PR reviews from GitHub API...')\nprint('requests.exceptions.HTTPError: 403 Client Error: rate limit exceeded for url: https://api.github.com/repos/org/repo/pulls')\nprint('X-RateLimit-Remaining: 0, X-RateLimit-Reset: 1711065600')\nsys.exit(1)\n"],
        "GitHub API rate limit exceeded"),
    TestCase("oidc-issuer-fail", 3, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Discovering OIDC configuration...')\nprint('requests.exceptions.ConnectionError: HTTPSConnectionPool(host=\\'idp.corp.local\\', port=443): Max retries exceeded with url: /.well-known/openid-configuration')\nsys.exit(1)\n"],
        "OIDC issuer discovery failure"),
    TestCase("ssh-key-rejected", 3, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Connecting to git@gitlab.internal via SSH...')\nprint('paramiko.ssh_exception.AuthenticationException: Authentication failed.')\nprint('Permission denied (publickey).')\nsys.exit(1)\n"],
        "SSH key rejected by Git server"),

    # ── Level 4: Data & Schema errors (10) ───────────────────────────
    TestCase("json-parse-error", 4, "python:3.13-slim", ["python3", "-c",
        "import json, time\ntime.sleep(2)\nprint('Parsing webhook payload...')\njson.loads('{invalid json}')\n"],
        "Python JSON parse error"),
    TestCase("db-migration-fail", 4, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Running database migration 0042_add_user_preferences...')\nprint('alembic.util.exc.CommandError: Target database is not up to date.')\nprint('sqlalchemy.exc.ProgrammingError: (psycopg2.errors.UndefinedColumn) column \"preferences\" of relation \"users\" does not exist')\nsys.exit(1)\n"],
        "Database migration failure"),
    TestCase("protobuf-decode", 4, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Decoding protobuf message from event stream...')\nprint('google.protobuf.message.DecodeError: Error parsing message with type \"events.OrderCreated\"')\nsys.exit(1)\n"],
        "Protobuf decode error"),
    TestCase("avro-schema-mismatch", 4, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Consuming from Kafka topic orders.created...')\nprint('confluent_kafka.error.SerializationError: Schema mismatch: writer schema ID 42 != reader schema ID 45')\nsys.exit(1)\n"],
        "Avro schema mismatch in Kafka"),
    TestCase("xml-malformed", 4, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Parsing SOAP response from legacy ERP...')\nprint('xml.etree.ElementTree.ParseError: not well-formed (invalid token): line 847, column 23')\nsys.exit(1)\n"],
        "Malformed XML from SOAP API"),
    TestCase("encoding-error", 4, "python:3.13-slim", ["python3", "-c",
        "import time\ntime.sleep(2)\nprint('Processing CSV upload (user_export_20260320.csv)...')\nb'\\xff\\xfe'.decode('utf-8')\n"],
        "Python UnicodeDecodeError on CSV"),
    TestCase("yaml-syntax", 4, "python:3.13-slim", ["python3", "-c",
        "import yaml, time\ntime.sleep(2)\nprint('Loading Kubernetes manifest...')\nyaml.safe_load('key: [invalid: yaml: {{')\n"],
        "YAML syntax error"),
    TestCase("graphql-validation", 4, "node:22-alpine", ["node", "-e",
        "setTimeout(()=>{console.log('Executing GraphQL query...');console.error('GraphQLError: Cannot query field \"nonExistentField\" on type \"User\".');console.error('  at /app/src/resolvers/user.ts:42:5');process.exit(1)},2000)"],
        "GraphQL validation error"),
    TestCase("csv-column-count", 4, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Importing transactions from SFTP...')\nprint('pandas.errors.ParserError: Error tokenizing data. C error: Expected 12 fields in line 8423, saw 14')\nsys.exit(1)\n"],
        "CSV column count mismatch"),
    TestCase("msgpack-corrupt", 4, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Deserializing cached session from Redis...')\nprint('msgpack.exceptions.UnpackValueError: Unpack failed: incomplete input at position 2847')\nsys.exit(1)\n"],
        "Msgpack deserialization failure"),

    # ── Level 5: Resource exhaustion (10) ────────────────────────────
    TestCase("oom-python", 5, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Loading ML model into memory...')\nprint('Processing batch of 50000 records...')\ndata = []\nfor i in range(10**8):\n    data.append(b'x' * 1024)\n"],
        "Python OOM loading ML model"),
    TestCase("disk-full", 5, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Writing audit log to /var/log/app/audit.log...')\nprint('OSError: [Errno 28] No space left on device: \\'/var/log/app/audit.log\\'')\nsys.exit(1)\n"],
        "Disk full writing audit log"),
    TestCase("fd-limit", 5, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Accepting connections on :8080...')\nprint('OSError: [Errno 24] Too many open files')\nprint('Current ulimit: 1024, active connections: 1024')\nsys.exit(1)\n"],
        "File descriptor limit reached"),
    TestCase("thread-limit", 5, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Spawning worker threads...')\nprint('RuntimeError: can\\'t start new thread (pthread_create failed: Resource temporarily unavailable)')\nsys.exit(1)\n"],
        "Thread creation limit"),
    TestCase("conn-pool-exhausted", 5, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Acquiring database connection from pool...')\nprint('sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 30.00')\nsys.exit(1)\n"],
        "DB connection pool exhausted"),
    TestCase("tmpdir-full", 5, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Extracting uploaded archive to /tmp...')\nprint('OSError: [Errno 28] No space left on device: \\'/tmp/extract_a8f3c2/data.bin\\'')\nsys.exit(1)\n"],
        "Temp directory full during extraction"),
    TestCase("heap-oom-node", 5, "node:22-alpine", ["node", "-e",
        "setTimeout(()=>{console.log('Processing large dataset...');const a=[];while(true){a.push(new Array(1e6).fill('x'))}},2000)"],
        "Node.js heap out of memory"),
    TestCase("ephemeral-storage", 5, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Container ephemeral storage exceeded')\nprint('The node was low on resource: ephemeral-storage. Container app was using 2Gi, which exceeds its request of 1Gi.')\nsys.exit(1)\n"],
        "Ephemeral storage limit exceeded"),
    TestCase("shm-full", 5, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Loading PyTorch model with DataLoader workers...')\nprint('RuntimeError: DataLoader worker (pid 42) is killed by signal: Bus error. It is possible that dataloader\\'s workers are out of shared memory.')\nsys.exit(1)\n"],
        "Shared memory full for PyTorch DataLoader"),
    TestCase("pvc-not-bound", 5, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Mounting data volume at /data...')\nprint('ERROR: /data is not mounted or empty. Expected PersistentVolumeClaim data-pvc to be bound.')\nsys.exit(1)\n"],
        "PVC not bound"),

    # ── Level 6: Application logic errors (10) ───────────────────────
    TestCase("null-pointer-java", 6, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('java.lang.NullPointerException')\nprint('\\tat com.company.service.UserService.getProfile(UserService.java:142)')\nprint('\\tat com.company.api.UserController.handleRequest(UserController.java:58)')\nprint('\\tat org.springframework.web.servlet.FrameworkServlet.service(FrameworkServlet.java:897)')\nsys.exit(1)\n"],
        "Java NullPointerException in UserService"),
    TestCase("division-by-zero", 6, "python:3.13-slim", ["python3", "-c",
        "import time\ntime.sleep(2)\nprint('Calculating average response time...')\ntotal_requests = 0\ntotal_time = 4532.7\navg = total_time / total_requests\n"],
        "Python ZeroDivisionError"),
    TestCase("index-out-of-range", 6, "python:3.13-slim", ["python3", "-c",
        "import time\ntime.sleep(2)\nprint('Processing batch results...')\nresults = [{'status': 'ok'}, {'status': 'error'}]\nprint(results[5])\n"],
        "Python IndexError"),
    TestCase("type-error-go", 6, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('goroutine 1 [running]:')\nprint('runtime: panic: runtime error: invalid memory address or nil pointer dereference')\nprint('[signal SIGSEGV: segmentation violation code=0x1 addr=0x0 pc=0x7a3f20]')\nprint('')\nprint('goroutine 1 [running]:')\nprint('main.(*Server).handleRequest(0x0, 0xc000142000)')\nprint('\\t/app/server.go:89 +0x40')\nsys.exit(2)\n"],
        "Go nil pointer dereference"),
    TestCase("stack-overflow", 6, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Processing recursive category tree...')\ndef process(node, depth=0):\n    return process(node, depth+1)\nprocess('root')\n"],
        "Python RecursionError (stack overflow)"),
    TestCase("assertion-error", 6, "python:3.13-slim", ["python3", "-c",
        "import time\ntime.sleep(2)\nprint('Validating order total...')\norder_total = -15.99\nassert order_total >= 0, f'Order total cannot be negative: {order_total}'\n"],
        "Python AssertionError on invalid order"),
    TestCase("key-error-config", 6, "python:3.13-slim", ["python3", "-c",
        "import time\ntime.sleep(2)\nconfig = {'database': {'host': 'localhost'}}\nprint('Reading replication config...')\nreplica = config['database']['replica_host']\n"],
        "Python KeyError in nested config"),
    TestCase("rust-panic", 6, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint(\"thread 'main' panicked at 'called `Result::unwrap()` on an `Err` value: ParseIntError { kind: InvalidDigit }', src/config.rs:27:48\")\nprint('note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace')\nsys.exit(101)\n"],
        "Rust panic on config parse"),
    TestCase("unhandled-promise", 6, "node:22-alpine", ["node", "-e",
        "setTimeout(()=>{console.log('Processing webhook event...');Promise.reject(new Error('Cannot read properties of undefined (reading \"id\")')).catch(e=>{console.error('UnhandledPromiseRejection:',e.message);process.exit(1)})},2000)"],
        "Node.js unhandled promise rejection"),
    TestCase("deadlock-detected", 6, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('ERROR: deadlock detected')\nprint('DETAIL: Process 4521 waits for ShareLock on transaction 789012; blocked by process 4519.')\nprint('Process 4519 waits for ShareLock on transaction 789011; blocked by process 4521.')\nprint('HINT: See server log for query details.')\nsys.exit(1)\n"],
        "PostgreSQL deadlock detected"),

    # ── Level 7: Dependency & version issues (10) ────────────────────
    TestCase("module-not-found", 7, "python:3.13-slim", ["python3", "-c",
        "import time\ntime.sleep(2)\nprint('Starting application...')\nimport nonexistent_module\n"],
        "Python ModuleNotFoundError"),
    TestCase("node-module-missing", 7, "node:22-alpine", ["node", "-e",
        "setTimeout(()=>{try{require('express')}catch(e){console.error(e.message);process.exit(1)}},2000)"],
        "Node.js cannot find module express"),
    TestCase("libc-missing", 7, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Loading native extension...')\nprint('ImportError: libpq.so.5: cannot open shared object file: No such file or directory')\nsys.exit(1)\n"],
        "Missing shared library libpq"),
    TestCase("python-version", 7, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint(f'Python {sys.version}')\nprint('SyntaxError: future feature annotations is not defined')\nprint('This package requires Python >= 3.14')\nsys.exit(1)\n"],
        "Python version incompatibility"),
    TestCase("pip-conflict", 7, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('ERROR: pip\\'s dependency resolver found conflicting dependencies:')\nprint('  package-a 2.0 requires numpy>=1.24, but you have numpy 1.21.0')\nprint('  package-b 1.5 requires numpy<1.23, but you have numpy 1.21.0')\nsys.exit(1)\n"],
        "Pip dependency conflict"),
    TestCase("java-class-version", 7, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Error: LinkageError occurred while loading main class com.company.App')\nprint('java.lang.UnsupportedClassVersionError: com/company/App has been compiled by a more recent version of the Java Runtime (class file version 65.0), this version of the Java Runtime only recognizes class file versions up to 61.0')\nsys.exit(1)\n"],
        "Java class version mismatch"),
    TestCase("npm-peer-dep", 7, "node:22-alpine", ["node", "-e",
        "setTimeout(()=>{console.error('Error: Could not find peer dependency react@^17.0.0');console.error('Found react@19.2.4 which does not satisfy the required range');process.exit(1)},2000)"],
        "npm peer dependency conflict"),
    TestCase("go-module-404", 7, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('go: downloading github.com/company/internal-lib v1.4.2')\nprint('go: github.com/company/internal-lib@v1.4.2: reading https://proxy.golang.org/github.com/company/internal-lib/@v/v1.4.2.info: 410 Gone')\nsys.exit(1)\n"],
        "Go module not found (private repo)"),
    TestCase("native-ext-build", 7, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Building wheel for cryptography (pyproject.toml) ... error')\nprint('error: subprocess-exited-with-error: cargo rustc failed with exit status 101')\nprint('error[E0463]: can\\'t find crate for `openssl_sys`')\nsys.exit(1)\n"],
        "Native extension build failure"),
    TestCase("image-entrypoint", 7, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('exec: \\'./start.sh\\': Permission denied')\nprint('OCI runtime exec failed: exec failed: unable to start container process')\nsys.exit(126)\n"],
        "Container entrypoint permission denied"),

    # ── Level 8: Network & DNS (10) ──────────────────────────────────
    TestCase("dns-nxdomain", 8, "python:3.13-slim", ["python3", "-c",
        "import socket, time\ntime.sleep(2)\nprint('Resolving internal-api.prod.svc.cluster.local...')\nsocket.getaddrinfo('internal-api.prod.svc.cluster.local', 8080)\n"],
        "DNS NXDOMAIN for cluster service"),
    TestCase("network-unreachable", 8, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Connecting to external payment processor...')\nprint('OSError: [Errno 101] Network is unreachable')\nprint('Attempted connection to 203.0.113.50:443')\nsys.exit(1)\n"],
        "Network unreachable to external API"),
    TestCase("tcp-reset", 8, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Streaming events from SSE endpoint...')\nprint('ConnectionResetError: [Errno 104] Connection reset by peer')\nprint('This typically indicates the server closed the connection unexpectedly')\nsys.exit(1)\n"],
        "TCP connection reset by peer"),
    TestCase("proxy-502", 8, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Calling upstream via envoy sidecar...')\nprint('requests.exceptions.HTTPError: 502 Server Error: Bad Gateway for url: http://localhost:15001/api/v1/users')\nprint('upstream connect error or disconnect/reset before headers. reset reason: connection failure')\nsys.exit(1)\n"],
        "Envoy proxy 502 Bad Gateway"),
    TestCase("mtls-handshake", 8, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Establishing mTLS connection...')\nprint('ssl.SSLError: [SSL: SSLV3_ALERT_HANDSHAKE_FAILURE] sslv3 alert handshake failure (_ssl.c:1000)')\nprint('Client certificate CN=app.demo.svc does not match required CN=app.production.svc')\nsys.exit(1)\n"],
        "mTLS handshake failure wrong cert"),
    TestCase("ipv6-only-fail", 8, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Binding to [::]:8080...')\nprint('OSError: [Errno 99] Cannot assign requested address')\nprint('IPv6 is not available on this node')\nsys.exit(1)\n"],
        "IPv6 bind failure"),
    TestCase("service-mesh-err", 8, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Istio sidecar injection detected')\nprint('upstream connect error or disconnect/reset before headers. retried and the latest reset reason: remote connection failure, transport failure reason: delayed connect error: 111')\nsys.exit(1)\n"],
        "Istio service mesh upstream failure"),
    TestCase("headless-svc-err", 8, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Discovering peers via headless service etcd.default.svc...')\nprint('etcdserver: request timed out, possibly due to connection lost')\nprint('WARNING: member 8e9e05c52164694d is unreachable')\nsys.exit(1)\n"],
        "Headless service peer discovery failure"),
    TestCase("ingress-loop", 8, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Health check failed: GET http://localhost:8080/healthz')\nprint('Error: too many redirects (max 10)')\nprint('Redirect chain: /healthz -> /login -> /healthz -> /login -> ...')\nsys.exit(1)\n"],
        "Redirect loop on health check"),
    TestCase("calico-deny", 8, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Connecting to postgres.production.svc:5432...')\nprint('psycopg2.OperationalError: could not connect to server: Connection timed out')\nprint('Is the server running on host \"postgres.production.svc\" and accepting TCP/IP connections on port 5432?')\nprint('Note: NetworkPolicy may be blocking egress from namespace demo')\nsys.exit(1)\n"],
        "NetworkPolicy blocking DB connection"),

    # ── Level 9: Startup & init failures (10) ────────────────────────
    TestCase("port-in-use", 9, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Starting HTTP server on :8080...')\nprint('OSError: [Errno 98] Address already in use: (\\'::\\', 8080)')\nprint('Another process is already listening on port 8080')\nsys.exit(1)\n"],
        "Port 8080 already in use"),
    TestCase("readiness-timeout", 9, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Waiting for database to be ready...')\nfor i in range(30):\n    print(f'  Attempt {i+1}/30: Connection refused')\nprint('FATAL: Database not ready after 30 attempts. Exiting.')\nsys.exit(1)\n"],
        "Readiness check timeout waiting for DB"),
    TestCase("init-container-fail", 9, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Running database schema validation...')\nprint('ERROR: Schema version mismatch!')\nprint('  Expected: v42 (from migration files)')\nprint('  Actual:   v39 (in database)')\nprint('Run \"flask db upgrade\" to apply pending migrations')\nsys.exit(1)\n"],
        "Schema validation failed in init"),
    TestCase("config-syntax", 9, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Loading nginx.conf...')\nprint('nginx: [emerg] unknown directive \"upstram\" in /etc/nginx/conf.d/default.conf:12')\nprint('nginx: configuration file /etc/nginx/nginx.conf test failed')\nsys.exit(1)\n"],
        "Nginx config syntax error"),
    TestCase("secret-not-mounted", 9, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Reading TLS certificate from /etc/tls/tls.crt...')\nprint('FileNotFoundError: [Errno 2] No such file or directory: \\'/etc/tls/tls.crt\\'')\nprint('Expected Secret \\'app-tls\\' to be mounted at /etc/tls/')\nsys.exit(1)\n"],
        "TLS secret not mounted"),
    TestCase("configmap-missing", 9, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Loading feature flags from /etc/config/features.yaml...')\nprint('FileNotFoundError: [Errno 2] No such file or directory: \\'/etc/config/features.yaml\\'')\nprint('ConfigMap \\'feature-flags\\' not found in namespace demo')\nsys.exit(1)\n"],
        "ConfigMap not mounted"),
    TestCase("crd-not-installed", 9, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Initializing custom resource controller...')\nprint('kubernetes.client.exceptions.ApiException: (404)')\nprint('Reason: Not Found')\nprint('the server could not find the requested resource (get certificates.cert-manager.io)')\nsys.exit(1)\n"],
        "CRD not installed"),
    TestCase("webhook-rejected", 9, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Creating Deployment in namespace production...')\nprint('Error from server: admission webhook \"validate.kyverno.svc-fail\" denied the request:')\nprint('policy require-labels/check-team-label: validation error: label \\'team\\' is required on all Deployments')\nsys.exit(1)\n"],
        "Admission webhook rejected deployment"),
    TestCase("priority-preempted", 9, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Pod was preempted by higher priority workload')\nprint('Preempted by: batch-job-critical-xyz in namespace batch')\nprint('Priority class: system-cluster-critical (2000000000) > default (0)')\nsys.exit(1)\n"],
        "Pod preempted by higher priority workload"),
    TestCase("sidecar-not-ready", 9, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Waiting for Envoy sidecar to be ready...')\nprint('GET http://localhost:15021/healthz/ready => connection refused')\nprint('Istio sidecar is not ready after 30s. Application cannot start without mesh connectivity.')\nsys.exit(1)\n"],
        "Istio sidecar not ready"),

    # ── Level 10: Catastrophic / multi-language (10) ─────────────────
    TestCase("segfault-c", 10, "python:3.13-slim", ["python3", "-c",
        "import time, sys, signal\ntime.sleep(2)\nprint('Loading native FFI module...')\nprint('Segmentation fault (core dumped)')\nprint('Signal 11 (SIGSEGV), address not mapped to object')\nsys.exit(139)\n"],
        "Segfault in native C extension"),
    TestCase("kernel-oom", 10, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Container killed by kernel OOM killer')\nprint('Memory cgroup out of memory: Killed process 1 (python3) total-vm:2548312kB, anon-rss:1048576kB')\nprint('oom_score_adj: 1000')\nsys.exit(137)\n"],
        "Kernel OOM killer (exit 137)"),
    TestCase("data-corruption", 10, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('PANIC: WAL segment 0000000100000001000000A3 has incorrect checksum')\nprint('DETAIL: Calculated CRC 0x3F2A1B4C but expected 0x7D8E9F0A')\nprint('HINT: Data directory might be corrupted. Restore from backup.')\nsys.exit(1)\n"],
        "PostgreSQL WAL corruption"),
    TestCase("etcd-corrupt", 10, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('etcdserver: panic: runtime error: failed to find member in raft cluster')\nprint('goroutine 1 [running]:')\nprint('go.etcd.io/etcd/server/v3/etcdserver.(*EtcdServer).applyEntries(0xc000a8c000)')\nsys.exit(2)\n"],
        "etcd cluster corruption"),
    TestCase("cert-chain-broken", 10, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Verifying certificate chain for *.prod.internal...')\nprint('x509: certificate signed by unknown authority')\nprint('Intermediate CA certificate expired: NotAfter 2026-03-19T00:00:00Z')\nprint('Full chain: leaf -> intermediate (EXPIRED) -> root')\nsys.exit(1)\n"],
        "Certificate chain broken - intermediate expired"),
    TestCase("race-condition", 10, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('FATAL: inconsistent state detected in distributed lock')\nprint('Lock owner: pod-a-7f8b9c acquired at T1')\nprint('Conflicting write: pod-b-3d4e5f acquired at T1+2ms')\nprint('Split-brain detected. Shutting down to prevent data loss.')\nsys.exit(1)\n"],
        "Distributed lock split-brain"),
    TestCase("gpu-not-found", 10, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Initializing CUDA runtime...')\nprint('RuntimeError: No CUDA GPUs are available')\nprint('torch.cuda.device_count() returned 0')\nprint('Expected nvidia.com/gpu resource but none scheduled on this node')\nsys.exit(1)\n"],
        "CUDA GPU not available"),
    TestCase("clock-skew", 10, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Validating JWT token...')\nprint('jose.exceptions.JWTClaimsError: Token used before its \"nbf\" claim')\nprint('Server time: 2026-03-20T17:00:00Z')\nprint('Token nbf:   2026-03-20T17:05:00Z')\nprint('Clock skew detected: node time is 5 minutes behind')\nsys.exit(1)\n"],
        "JWT validation failed due to clock skew"),
    TestCase("zombie-process", 10, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('Health check failed: process 1 (tini) has 847 zombie children')\nprint('PID 1 is not reaping child processes')\nprint('Container is in degraded state, restarting...')\nsys.exit(1)\n"],
        "Zombie process accumulation"),
    TestCase("multi-lang-panic", 10, "python:3.13-slim", ["python3", "-c",
        "import time, sys\ntime.sleep(2)\nprint('=== Polyglot Service Failure ===')\nprint('[Python] ImportError: cannot import name \\'deprecated_func\\' from \\'core.legacy\\'')\nprint('[JNI] java.lang.NoSuchMethodError: com.company.Bridge.processV2()V')\nprint('[CGo] fatal error: unexpected signal during runtime execution')\nprint('[WASM] RuntimeError: unreachable executed at offset 0x1a3f')\nsys.exit(1)\n"],
        "Multi-language polyglot service cascade failure"),
]
# fmt: on


def generate_yaml(tc: TestCase) -> str:
    cmd_json = json.dumps(tc.command)
    return f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {tc.name}
  namespace: {NAMESPACE}
  labels:
    app: klarsicht-test
    test-level: "{tc.level}"
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {tc.name}
  template:
    metadata:
      labels:
        app: {tc.name}
    spec:
      containers:
        - name: app
          image: {tc.image}
          command: {cmd_json}
          resources:
            requests:
              cpu: 10m
              memory: 32Mi
            limits:
              cpu: 100m
              memory: 64Mi
"""


def kubectl(args: str, check: bool = True) -> str:
    result = subprocess.run(
        f"kubectl {args}", shell=True, capture_output=True, text=True
    )
    if check and result.returncode != 0:
        print(f"  kubectl error: {result.stderr.strip()}", file=sys.stderr)
    return result.stdout.strip()


def get_incidents() -> dict:
    try:
        resp = urllib.request.urlopen(f"{LOCAL_API}/incidents", timeout=5)
        return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  Failed to fetch incidents: {e}", file=sys.stderr)
        return {}


def get_incident_count() -> int:
    return len(get_incidents())


def wait_for_new_incident(prev_count: int, timeout: int = 180) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        current = get_incident_count()
        if current > prev_count:
            return True
        time.sleep(10)
    return False


def run_test(tc: TestCase, index: int, total: int) -> bool:
    print(f"\n[{index}/{total}] Level {tc.level} — {tc.name}")
    print(f"  {tc.description}")

    yaml_content = generate_yaml(tc)
    yaml_path = f"/tmp/klarsicht-test-{tc.name}.yaml"
    with open(yaml_path, "w") as f:
        f.write(yaml_content)

    prev_count = get_incident_count()

    # Deploy
    print("  Deploying...", end="", flush=True)
    kubectl(f"apply -f {yaml_path}")
    print(" done")

    # Wait for CrashLoopBackOff
    print("  Waiting for CrashLoopBackOff...", end="", flush=True)
    for _ in range(24):  # 2 minutes
        status = kubectl(f"get pod -n {NAMESPACE} -l app={tc.name} -o jsonpath='{{.items[0].status.containerStatuses[0].state.waiting.reason}}'", check=False)
        if "CrashLoopBackOff" in status:
            print(f" detected")
            break
        time.sleep(5)
    else:
        # Check if terminated instead
        phase = kubectl(f"get pod -n {NAMESPACE} -l app={tc.name} -o jsonpath='{{.items[0].status.phase}}'", check=False)
        print(f" (pod phase: {phase})")

    # Wait for Klarsicht to pick it up
    print("  Waiting for investigation...", end="", flush=True)
    found = wait_for_new_incident(prev_count, timeout=180)
    if found:
        print(" received!")
    else:
        print(" TIMEOUT — not detected")

    # Cleanup
    print("  Cleaning up...", end="", flush=True)
    kubectl(f"delete -f {yaml_path} --wait=false")
    subprocess.run(f"rm -f {yaml_path}", shell=True)
    print(" done")

    return found


def main():
    # Check connectivity
    print("Checking Klarsicht API connectivity...")
    try:
        resp = urllib.request.urlopen(f"{LOCAL_API}/healthz", timeout=5)
        print(f"  API: {json.loads(resp.read().decode())}")
    except Exception as e:
        print(f"  Cannot reach {LOCAL_API}/healthz: {e}")
        print("  Run: kubectl port-forward svc/klarsicht-agent -n klarsicht 8000:8000")
        sys.exit(1)

    initial_count = get_incident_count()
    print(f"  Existing incidents: {initial_count}")
    print(f"  Total test cases: {len(TESTS)}")
    print(f"  Levels: 1-10, {len(TESTS)//10} tests per level\n")

    results = {"passed": 0, "failed": 0, "errors": []}

    for i, tc in enumerate(TESTS, 1):
        try:
            success = run_test(tc, i, len(TESTS))
            if success:
                results["passed"] += 1
            else:
                results["failed"] += 1
                results["errors"].append(tc.name)
        except KeyboardInterrupt:
            print("\n\nInterrupted! Cleaning up...")
            kubectl(f"delete deployment -n {NAMESPACE} -l app=klarsicht-test --wait=false", check=False)
            break
        except Exception as e:
            print(f"  ERROR: {e}")
            results["failed"] += 1
            results["errors"].append(tc.name)
            kubectl(f"delete deployment -n {NAMESPACE} {tc.name} --wait=false", check=False)

    # Summary
    final_count = get_incident_count()
    print("\n" + "=" * 60)
    print(f"RESULTS: {results['passed']} detected / {results['passed'] + results['failed']} run")
    print(f"New incidents created: {final_count - initial_count}")
    if results["errors"]:
        print(f"Failed: {', '.join(results['errors'])}")
    print("=" * 60)


if __name__ == "__main__":
    main()
