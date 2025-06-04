## mcp-server-python

This is an MCP server that provides tools and resources to call the Fulcra API from LLMs.  

It handles the OAuth2 callback, but doesn't leak the exchanged tokens to MCP clients.  Instead, it keeps a mapping table in memory.
