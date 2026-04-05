# gitea mcp server

## description

gitea-mcp-server will provide the agent with tools and skills to fully interact
with the gitea/forgejo api and be able to administer a gitea/forgejo server,
manage and work with repositories, organizations, users and their settings.

The server code will be built using latest best practices for coding, using
modules and concepts. TDD will ensure that code will do what we expect it to do
and prevent regressions.

## external resources

- fastmcp3.2.0: https://gofastmcp.com/llms.txt
- gitea swagger: https://git.home.lan/swagger.v1.json
- openapi schema: https://spec.openapis.org/oas/3.1/schema/2025-11-23

## to be used

- python3
- fastmcp3.2.0
- httpx
- pydantic

## .venv for local python envs

We're using `mise` and `.mise.toml` to manage tools and python virtual envs. This `.venv` must be activated at the start of a sessions. The system's python set up will not be touched

## challenges

### fastmcp2.0 can auto generate tools from swagger openapi.v3

gitea/forgejo has swagger, but it's openapi.v2. Therefore a converter has to be
build to take in the swagger from gitea/forgejo and to present it to fastmcp2.0
as openapi.v3. This means endpoint, parameters, responses and definitions.
Everything.

When a openapi.v3 swagger is detected, the converter will be by-passed

### auto-generated tools and pre-written examples and descriptions

MCP server best practices dictates good descriptions and parameter uses, do's and don't's and examples. This is of course not possible when auto-generating tools, as it's beforehand not 'known' which tools will be auto-generated. Making tool 'man-pages' available via resources has the same problem. This means that the documents made available through resources will be on a more conceptual level, either per 'skill' or 'role', maybe 'workflow'.

### testing during development

The GITEA_URL and GITEA_TOKEN env vars will enable the agent to interact with the user's repos, including the gitea repo that is remote for this local repository.  It will happen that the agent has to live test its new tools and other facilities with possibly destructive actions. It is important that this will not happen on the production server represented by GITEA_URL and GITEA_TOKEN.  The gitea-mcp-server will need facilities that will enable the agent to test tools on a test gitea instance. A way has to be found to live test tools on a test instance.

## others

- tools will be made available as snake_case
- custom tools will override auto-generated tools with the same name
- mcp resources will contain descriptions and examples following 'skill' format
- resource 'skills' will be made available via context injection
- Context injection will provide the agent with just enough context to be aware
  of the gitea mcp tools and resources and some discovery paths
- the server will be available through its stdio, http sse and http-streamable interfaces.
- gitea-mcp-server will behave as a good cli citizen, obeying TERM signals, log as is expected
- the class user of the gitea-mcp-server is the agent. The tools and resources
  need to be as convinient as possible to use, its implementation must feel
  'intuitive' for the agent.
- tools should be 'invisible' ('disabled'/'unsupported) when they are not
  available: for example, when a token does not have 'admin' enabled, the admin
  tools shouldn't be listed: it's not efficient, blurs the context, wastes
  tokens.  Another example is 'wiki': if the server or repo doesn't have wiki
  enabled, the wiki tools shouldn't be available.
- As soon as the basic server is working, all efforts go into containerization
  of the project. It must be possible to restart the gitea-mcp-server without
  restarting the agent software (opencode, claude).
- A docker-compose.yml for a development/test gitea server with sqlite3 backend is needed.
- The gitea-mcp-server will take in env vars GITEA_TOKEN and GITEA_URL and use
  those to access the gitea server with mcp tools.
- The gitea-mcp-server will take in env var SSL_CERT_FILE and use it for python
  to recognize the system's ca-certificates. This will enable a local ca.
- a 'environment cache' resource will be available with 'discovered data': username, organizations, repositories.

