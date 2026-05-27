# Bandwidth Official MCP Server
Source code for the official Bandwidth Model Context Protocol (MCP) Server.
This server can be used to interact with different Bandwidth APIs via an AI agent.
The server is provided as a python package and may be cloned directly from this repo.

## Installation

Clone directly from this git repository using:

```shell
git clone https://github.com/Bandwidth/mcp-server.git
cd mcp-server
```

## Getting Started

### Prerequisites

In order to use the Bandwidth MCP Server, you'll need the following things, set as environment variables.
- Valid Bandwidth OAuth2 Client Credentials
    - You will need a client ID and client secret for your Bandwidth API application
    - For more info on creating API credentials, see our [Credentials](https://dev.bandwidth.com/docs/credentials) page
- Your account ID is auto-discovered from JWT claims after authentication — you do not need to provide it

### Configuration

#### Environment Variables

Environment variables are used to configure the Bandwidth MCP Server.
The server will respect both system environment variables and those configured via your AI agent.

The following variables will be required to use the server:

```sh
BW_CLIENT_ID     # Your Bandwidth OAuth2 client ID
BW_CLIENT_SECRET # Your Bandwidth OAuth2 client secret
```

The following variables are optional or conditionally required:

```sh
BW_ACCOUNT_ID               # Your Bandwidth Account ID. Optional — auto-discovered from JWT claims after authentication.
BW_NUMBER                   # A valid phone number on your Bandwidth account. Used with our Messaging and MFA APIs. Must be in E164 format.
BW_MESSAGING_APPLICATION_ID # A Bandwidth Messaging Application ID. Used with our Messaging and MFA APIs.
BW_VOICE_APPLICATION_ID     # A Bandwidth Voice Application ID. Used with our MFA API.
BW_MCP_PROFILE              # Named tool preset (voice, messaging, mfa, lookup, onboarding, recordings, full). Comma-separated to combine.
BW_MCP_TOOLS                # Explicit tool allowlist (comma-separated operationIds). Overrides BW_MCP_PROFILE.
BW_MCP_EXCLUDE_TOOLS        # Explicit tool denylist (comma-separated). Takes priority over BW_MCP_TOOLS and profiles.
BW_ENVIRONMENT              # `test` or `uat` to target Bandwidth's test environment. Defaults to prod.
BW_API_URL                  # API gateway override. Also serves the Dashboard XML API under /api/v2.
BW_VOICE_URL                # Voice API base override.
BW_MESSAGING_URL            # Messaging API base override.
```

#### Including or Excluding Tools

By default, the server provides and enables all tools listed in the [Tools List](#tools-list).
Enabling all of these tools may cause context window size issues for certain AI agents or lead to slower agent response times.
To work around this and for a better experience, we recommend enabling only the certain subset of tools you plan on using.

This can be accomplished by supplying a list of tool names to specifically enable or exclude to the server.
This list must be comma separated, with the tool names matching their names in the [Tools List](#tools-list).
The `BW_MCP_TOOLS` and `BW_MCP_EXCLUDE_TOOLS` mentioned in the [Environment Variables](#environment-variables)
section allow for enabling and excluding tools by name. You can also use the CLI flags `--tools` and `--exclude-tools`.
Using the CLI flags will take priority over the environment variables, and providing tools to exclude will take priority over the list of enabled tools.

For a more comprehensive list of common use cases when which tools are required for each, check out our
[Common Use Cases Guide](common_use_cases.md).

##### Tool Filtering Examples

**Including only our Messaging tools**

```sh
# Environment Variable
BW_MCP_TOOLS=listMessages,createMessage,createMultiChannelMessage

# CLI Flag
--tools listMessages,createMessage,createMultiChannelMessage
```

**Excluding our Phone Number Lookup Tools**

```sh
# Environment Variable
BW_MCP_EXCLUDE_TOOLS=createLookup,getLookupStatus

# CLI Flag
--exclude-tools createLookup,getLookupStatus
```

**Account Creation Flow (Express Registration)**

```sh
BW_MCP_TOOLS=createRegistration,sendVerificationCode,verifyRegistrationCode
```

## Using the Server

Below you'll find instructions for using our MCP server with different common AI agents, as well as instructions for running the server locally. For usage with AI agents, it is recommended to use a combination of [uv](https://github.com/astral-sh/uv?tab=readme-ov-file#uv) and environment variables to start and configure the server respectively.

### Goose CLI

1. Install [Goose CLI](https://block.github.io/goose/docs/getting-started/installation/)
    - We recommend configuring Goose to use `Allow Mode`. This will require user approval before Goose calls tools, which could prevent Goose from accidentally taking unwanted actions.
2. Add the Bandwidth MCP Server as a Command-line Extension

```shell
goose configure
```

Then follow the prompts like the example below.

```shell
┌   goose-configure
│
◇  What would you like to configure?
│  Add Extension
│
◇  What type of extension would you like to add?
│  Command-line Extension
│
◇  What would you like to call this extension?
│  bw-mcp-server
│
◇  What command should be run?
│  uvx --from /path/to/mcp-server start
```

> **_NOTE:_** If you configure environment variables with Goose, it will prioritize those over your system environment variables.

### Cursor

1. Install [Cursor](https://cursor.com/downloads)
2. Update your `.cursor/mcp.json` file to include the following object

```json
{
    "mcpServers": {
        "bw-mcp-server": {
            "command":"uvx",
            "args": ["--from", "/path/to/mcp-server", "start"],
            "env": {
                "BW_CLIENT_ID": "<insert-bw-client-id>",
                "BW_CLIENT_SECRET": "<insert-bw-client-secret>",
                "BW_MCP_TOOLS": "tools,to,enable",
                "BW_MCP_EXCLUDE_TOOLS": "tools,to,exclude",
            }
        }
    }
}
```

### VSCode (Copilot)

1. Within VSCode, open the Command Palette and search for `MCP: Add Server`.
2. Choose `Command (stdio)`, then enter the full command to start the server. (Example Below)

```shell
uvx --from /path/to/mcp-server start
```

3. Choose a name for the server (ie. `bw-mcp-server`) and select if you'd like it to be enabled Globally or only in the current workspace.
4. You can configure environment variables by opening the `mcp.json` file VSCode provides like the example below.

```json
{
    "servers": {
        "bw-mcp-server": {
            "type": "stdio",
            "command": "uvx",
            "args": ["--from", "/path/to/mcp-server", "start"],
            "env": {
                "BW_CLIENT_ID": "<insert-bw-client-id>",
                "BW_CLIENT_SECRET": "<insert-bw-client-secret>",
                "BW_MCP_TOOLS": "tools,to,enable",
                "BW_MCP_EXCLUDE_TOOLS": "tools,to,exclude",
            }
        }
    },
    "inputs": []
}
```

> **_NOTE:_** You may need to make sure MCP servers are enabled in VSCode to begin using the server. See the [official guide](https://code.visualstudio.com/docs/copilot/customization/mcp-servers) for more info.

### Claude Desktop

1. Install [Claude Desktop](https://claude.ai/download)
2. Edit your `claude_desktop_config.json` to include the following object

```json
{
    "mcpServers": {
        "Bandwidth": {
            "command": "uvx",
            "args": ["--from", "/path/to/mcp-server", "start"],
            "env": {
                "BW_CLIENT_ID": "<insert-bw-client-id>",
                "BW_CLIENT_SECRET": "<insert-bw-client-secret>",
                "BW_MCP_TOOLS": "tools,to,enable",
                "BW_MCP_EXCLUDE_TOOLS": "tools,to,exclude",
            }
        }
    }
}
```

> **_NOTE:_** We've noticed some issues with Claude not being able to see MCP resources. This could require you to manually enter some tool parameters normally included in our config resource.

### Running the Server Standalone

The MCP server can be run locally using either native python or uv.
When running this way, all environment variables MUST be set in your system environment.

#### Run Using Native Python

Running the server locally with a python [virtual environment](https://docs.python.org/3/library/venv.html) requires both [python](https://www.python.org/downloads/) and [pip](https://pip.pypa.io/en/stable/getting-started/). 
Once these are installed, create a virtual environment using:

```sh
python -m venv .venv
```

Then activate and install the project with its dependencies.

```sh
source .venv/bin/activate
pip install .
```

After all packages are installed in the virtual environment, you can run the server locally using:

```sh
python src/app.py
```

#### Run Using uv

Make sure you have [uv installed](https://github.com/astral-sh/uv?tab=readme-ov-file#installation),
then you can start the server by running the following command from the root directory of this repository.

```sh
uvx --from ./ start
```

## Hosted Mode

Run the server over HTTP to enable remote access and webhook callbacks:

```bash
BW_MCP_TRANSPORT=streamable-http \
BW_MCP_PORT=8080 \
BW_MCP_BASE_URL=https://your-server.example.com \
BW_CLIENT_ID=your_client_id \
BW_CLIENT_SECRET=your_client_secret \
python src/app.py
```

### Tool Profiles

Reduce context window pressure with named presets:

```bash
BW_MCP_PROFILE=messaging    # SMS/MMS tools only
BW_MCP_PROFILE=voice        # Voice + BXML tools
BW_MCP_PROFILE=onboarding   # Account creation
BW_MCP_PROFILE=lookup       # Number intelligence
BW_MCP_PROFILE=messaging,voice  # Combine profiles
```

Profiles set via `BW_MCP_PROFILE` env var or `--profile` CLI flag. Use `BW_MCP_TOOLS` to override with specific tool names.

## Tools List

Tools are grouped into profiles that mirror the workflows you'd use the server for.
Loading a single profile keeps your agent's context small. The full agent reference
— including auth model, error codes, and the "trust nothing" guidance for async
calls — lives in [`src/specs/AGENTS.md`](src/specs/AGENTS.md).

The default tool set is `voice` + `messaging` + `lookup` + `mfa` (plus
`setCredentials` / `clearCredentials`, always loaded). Override with
`BW_MCP_PROFILE`, `BW_MCP_TOOLS`, or `BW_MCP_EXCLUDE_TOOLS`.

### Session management (always loaded)
- `setCredentials` — authenticate the session (stdio transport only)
- `clearCredentials` — log out of the session

### Profile: `onboarding` (no auth required)
- `createRegistration` — start Express Registration with contact details
- `sendVerificationCode` — trigger SMS OTP to the registered number
- `verifyRegistrationCode` — confirm the OTP; returns a client ID / secret

### Profile: `voice`
- `listApplications` / `createApplication` — find or create a voice app
- `listPhoneNumbers` — find numbers on the account
- `createCall` — place an outbound call
- `getCallState` — read current call state (always poll after `createCall`)
- `listCalls` — list call events with filtering
- `updateCall` / `updateCallBxml` — redirect, hang up, or replace BXML
- `generateBXML` — build valid BXML from a verb list
- `respondToCallback` — queue a BXML response for an active callback (first-write-wins)
- `getCallbackEvents` — read recent voice / messaging callback events
- `configureCallbacks` — point an application's webhook URLs at this server

### Profile: `messaging`
- `createMessage` — send SMS / MMS
- `listMessages` — query message history
- `getInboundMessages` — read inbound messages captured by this server
- `listMedia` / `getMedia` / `uploadMedia` / `deleteMedia` — manage MMS media
- `configureCallbacks` — point an application's callbacks at this server

### Profile: `mfa`
- `generateMessagingCode` — send MFA code over SMS (full account)
- `generateVoiceCode` — send MFA code over voice (Build OK)
- `verifyCode` — validate a code the user entered

### Profile: `lookup`
- `createSyncLookup` — one-shot lookup for a small input set
- `createAsyncBulkLookup` — kick off a bulk lookup
- `getAsyncBulkLookup` — poll a bulk lookup

### Profile: `recordings`
- `listCallRecordings` / `getCallRecording` — list / inspect recordings
- `downloadCallRecording` — download the media
- `deleteRecording` — remove a recording
- `transcribeCallRecording` / `getRecordingTranscription` — request and read transcription

See [`src/specs/AGENTS.md`](src/specs/AGENTS.md) for argument-level guidance, polling
patterns, and the structured error shape the server returns on failure.
