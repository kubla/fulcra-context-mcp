## mcp-server-python

This is an MCP server that provides tools and resources to call the Fulcra API using `fulcra-api`.

When run on its own (or when `FULCRA_ENVIRONMENT` is set to `stdio`), it acts as a local MCP server.  Otherwise, it acts as a remote server.  It handles the OAuth2 callback, but doesn't leak the exchanged tokens to MCP clients.  Instead, it keeps a mapping table in memory.

### Debugging

- Both the [MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector) and [mcp-remote](https://github.com/geelen/mcp-remote) tools can be useful in debugging.

