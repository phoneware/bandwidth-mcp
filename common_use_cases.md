# Common Use Cases

This guide outlines some common use cases for the MCP Server, as well as the tools required for these cases.
For more information on how to include the tools mentioned in this guide, please see the
[Including or Excluding Tools](README.md#including-or-excluding-tools) section in the README.

## Sending Text Messages

If you're looking to send messages using the MCP server, we recommend enabling the following tools:
- `listMessages` - Get info about messages you just sent or other messages on your account.
- `createMessage` - Send SMS or MMS messages
- `createMultiChannelMessage` - Send multi-channel messages (mostly for RBM messaging)

Sending messages requires `BW_ACCOUNT_ID`, `BW_MESSAGING_APPLICATION_ID`, and `BW_NUMBER` to be set in your environment variables.

**Enabling these tools**
```sh
# Environment Variable
BW_MCP_TOOLS=listMessages,createMessage,createMultiChannelMessage

# CLI Flag
--tools listMessages,createMessage,createMultiChannelMessage
```

## Looking up Telephone Numbers

If you'd like to get info about a specific telephone number or list of numbers,
you'll need both our `createLookup` and `getLookupStatus` tools.
Most agents we've experimented with have been smart enough to figure out that you
need to both create a lookup request and then get its' status to actually get the TN info,
and enabling only these two tools is a good way to help your agent remember that!

Phone Number Lookup requires the `BW_ACCOUNT_ID` environment variable to be set.

**Enabling these tools**
```sh
# Environment Variable
BW_MCP_TOOLS=createLookup,getLookupStatus

# CLI Flag
--tools createLookup,getLookupStatus
```

## Adding a Business End User

To add an end user, you'll need three specific Compliance endpoints.
- `listEndUserTypes` - Used to list all End user types and their required fields
- `listEndUserActivationRequirements` - Required if the end user will be used for requirements packages
- `createComplianceEndUser` - Used to create the end user

These tools will require the `BW_ACCOUNT_ID` environment variable.

**Enabling these tools**
```sh
# Environment Variable
BW_MCP_TOOLS=listEndUserTypes,listEndUserActivationRequirements,createComplianceEndUser
# CLI Flag
--tools listEndUserTypes,listEndUserActivationRequirements,createComplianceEndUser
```

## Address Validation

Validating an address can be done with just the `validateAddress` tool and the `BW_ACCOUNT_ID` environment variable!

**Enabling this tool**
```sh
# Environment Variable
BW_MCP_TOOLS=validateAddress
# CLI Flag
--tools validateAddress
```
