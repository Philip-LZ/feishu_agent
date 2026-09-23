FROM ghcr.io/agent-infra/sandbox:latest

RUN python -m pip install --no-cache-dir 'psycopg2-binary>=2.9' 'openai>=1.0' 'python-dotenv>=1.0'
