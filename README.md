## fulcra-context-mcp: An MCP server to access your Fulcra Context data

This is an MCP server that provides tools and resources to call the Fulcra API using `fulcra-api`.

There is a public instance of this server running at `https://mcp.fulcradynamics.com/mcp`.  See [https://fulcradynamics.github.io/developer-docs/mcp-server/](https://fulcradynamics.github.io/developer-docs/mcp-server/) to get started quickly.  This repo is primarily for users who need to run the server locally, want to see under the hood, or want to help contribute.

When run on its own (or when `FULCRA_ENVIRONMENT` is set to `stdio`), it acts as a local MCP server using the stdio transport.  Otherwise, it acts as a remote server using the Streamble HTTP transport.  It handles the OAuth2 callback, but doesn't leak the exchanged tokens to MCP clients.  Instead, it maintains a mapping table and runs its own OAuth2 serice between MCP clients.

### Debugging

- Both the [MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector) and [mcp-remote](https://github.com/geelen/mcp-remote) tools can be useful in debugging.

