"""Local Dev Server for Printosky Vercel APIs.

Bypasses Python circular import issues by importing api.index as a module.
"""
import os
import sys
from dotenv import load_dotenv

# Ensure root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# Load env variables
load_dotenv('.env')

# Import api.index cleanly (resolving circular dependencies in sys.modules)
import api.index
from http.server import HTTPServer

if __name__ == '__main__':
    port = 3008
    print(f"Starting Printosky Local API Dev Server on port {port}...")
    server = HTTPServer(('127.0.0.1', port), api.index.handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down API server...")
        server.server_close()
