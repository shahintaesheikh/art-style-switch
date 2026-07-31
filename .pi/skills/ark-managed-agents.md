---
name: byteplus-modelark-managed-agents
description: >
  BytePlus ModelArk Managed Agents framework integration. Use when the user needs to
  create, configure, deploy, or manage AI agents using BytePlus ModelArk's Managed Agents
  API (also known as Volcengine Ark / 火山方舟). Triggers include requests to "create an agent",
  "deploy a managed agent", "configure agent tools", "set up MCP", "manage agent sessions",
  "use Skills", "configure Multi-Agent coordination", or any enterprise agent deployment task.
---

# BytePlus ModelArk Managed Agents Framework

ModelArk Managed Agents is a fully managed AI Agent service by BytePlus (Volcengine Ark / 火山方舟).
It provides an out-of-the-box agent runtime where you define Agents as versioned resources,
configure their capabilities (Skills, Tools, MCP), attach them to sandboxed environments,
and run them via Sessions — all through REST APIs.

## Core Concepts

- **Agent**: A versioned configuration template containing basic info, system prompt, and extension capabilities (Skills, Tools, MCP). Agents are reusable across Sessions.
- **Environment**: A cloud sandbox configuration describing the runtime (packages, networking, env vars). Reusable across Sessions, each Session gets an isolated sandbox instance.
- **Session**: A single run of an Agent in an Environment. Sessions maintain conversation history and sandbox state across turns. Created in `idle` state, then receives events.
- **Skill**: A reusable capability pack (domain knowledge, workflows, best practices). Can be pre-built from SkillHub or custom uploaded.
- **Tool**: Execution capabilities the Agent can invoke (Bash, Read, Write, Edit, Web Search, etc.).
- **MCP (Model Context Protocol)**: Connect third-party systems (GitHub, Jira, etc.) as tools.
- **Vault**: Securely stores credentials (API keys, tokens) injected into Sessions.

## Prerequisites

1. **API Key**: Create at [API Key Management](https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey). Set as environment variable:
   ```bash
   export ARK_API_KEY="your_api_key_here"
   ```

2. **Model Access**: Enable models at [Open Management](https://console.volcengine.com/ark/region:ark+cn-beijing/openManagement).

3. **Base URL**: `https://ark.cn-beijing.volces.com/api/v3`

## API Reference

### Base URL & Authentication

All API calls use:
- **Base URL**: `https://ark.cn-beijing.volces.com/api/v3`
- **Auth Header**: `Authorization: Bearer $ARK_API_KEY`
- **Content-Type**: `application/json`

---

## 1. Agent Management

### 1.1 Create Agent

`POST /api/v3/agents`

Creates a new managed Agent. Returns an Agent ID (e.g., `agent-2026070207****-*****`) and initial version `1`.

**Request Body Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Agent name (1-100 chars, uppercase/lowercase letters, Chinese chars, digits) |
| `description` | string | No | Description (max 300 chars) |
| `model` | object | Yes | Model configuration |
| `model.id` | string | Yes | Model ID (e.g., `doubao-seed-2-1-pro-260628`) |
| `model.speed` | string | No | `standard` (default) or `fast` |
| `model.token_limits` | object | No | Token limit configuration |
| `model.token_limits.context_window` | integer | No | Context window size |
| `model.token_limits.max_input_token_length` | integer | No | Max input tokens |
| `model.token_limits.max_output_token_length` | integer | No | Max output tokens |
| `system` | string | No | System prompt defining agent role, behavior, and rules |
| `skills` | object[] | No | Skills the agent can auto-invoke |
| `skills[].type` | string | Yes | `skill_hub` (pre-built) or `custom` |
| `skills[].skill_id` | string | No | Skill ID from SkillHub or CreateSkill response |
| `skills[].version` | string | No | Specific skill version |
| `tools` | object[] | No | Tool collections the agent can use |
| `tools[].type` | string | Yes | `agent_toolset_20260701`, `evolution`, or `mcp_toolset` |
| `multiagent` | object | No | Multi-Agent coordination config |
| `mcp_servers` | object[] | No | MCP Server declarations |
| `metadata` | map[string]string | No | Custom key-value pairs (max 16 keys, 512 chars each) |

**Example — Create a News Agent with built-in tools and a Skill:**

```bash
curl https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "NewsAgent01",
    "model": {
      "id": "doubao-seed-2-1-pro-260628",
      "speed": "standard"
    },
    "description": "A news summarization assistant.",
    "system": "You are a news query assistant. Summarize the top 10 trending news items as summary images.",
    "skills": [
      {
        "type": "skill_hub",
        "skill_id": "s-yepozmp9tsf2adftt1hx"
      }
    ],
    "tools": [
      {
        "type": "agent_toolset_20260701"
      }
    ]
  }'
```

**Response:**

```json
{
  "id": "agent-2026070207****-*****",
  "type": "agent",
  "name": "NewsAgent01",
  "version": 1,
  "model": { "id": "doubao-seed-2-1-pro-260628", "speed": "standard" },
  "system": "You are a news query assistant...",
  "tools": [{"type": "agent_toolset_20260701", "default_config": {"enabled": true}}],
  "skills": [{"type": "skill_hub", "skill_id": "s-yepozmp9tsf2adftt1hx"}],
  "created_at": "2026-07-02T07:03:55Z",
  "updated_at": "2026-07-02T07:03:55Z"
}
```

### 1.2 Update Agent

`PUT /api/v3/agents/{agent_id}`

Updates an Agent's configuration. The request **must** include the current `version` number.
On success, the version auto-increments. Uses **overwrite semantics** for `skills` — pass the full desired array.

**Rules:**
- `version` is required — must match the current version
- Partial updates are supported: only send fields you want to change
- `skills` uses **overwrite** logic: send the complete list
- To add/remove a Skill, read current skills first, then write back the full set

```bash
curl https://ark.cn-beijing.volces.com/api/v3/agents/{agent_id} \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "version": 1,
    "system": "Updated system prompt here...",
    "skills": [
      {"type": "skill_hub", "skill_id": "s-yepozmp9tsf2adftt1hx"},
      {"type": "skill_hub", "skill_id": "s-yej50m054wyzaxeq073n"}
    ]
  }'
```

### 1.3 List/Get Agents

`GET /api/v3/agents` — List all Agents
`GET /api/v3/agents/{agent_id}` — Get a specific Agent's details

---

## 2. Skills

Skills are reusable capability packs for domain knowledge, workflows, and best practices.

### 2.1 Pre-built Skills (SkillHub)

Browse and select from [SkillHub](https://console.volcengine.com/skillhub). Reference the Skill ID in the Agent's `skills` array.

### 2.2 Custom Skills (CreateSkill)

`POST /api/v3/skills`

Upload custom Skills via Multipart or Zip.

**Upload Limits:**
- Zip file: max 50MB
- Max 500 files per version
- Single file: max 25MB (after extraction)
- Exactly 1 `SKILL.md` per bundle

**Directory structure:**
```
frontend-design.zip
└── frontend-design/
    ├── SKILL.md
    └── meta.json
```

**Multipart upload:**
```bash
curl https://ark.cn-beijing.volces.com/api/v3/skills \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -F "display_title=Web Artifacts Builder" \
  -F "files[]=@./basic_math/SKILL.md;filename=basic_math/SKILL.md;type=text/markdown" \
  -F "files[]=@./basic_math/scripts/init.sh;filename=basic_math/scripts/init.sh;type=text/plain"
```

**Zip upload:**
```bash
curl https://ark.cn-beijing.volces.com/api/v3/skills \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -F "files=@./basic_math.zip;type=application/zip"
```

**Response:**
```json
{
  "id": "skill-20260702082507-x6vpp",
  "object": "skill",
  "created_at": 1782980707,
  "latest_version": "1",
  "name": "minimal-news-skill"
}
```

---

## 3. Tools

Tools determine what execution capabilities the Agent can actively invoke during a Session.

### 3.1 Supported Tool Types

| Type | Description |
|------|-------------|
| `agent_toolset_20260701` | Built-in toolset (Bash, Read, Write, Edit, Glob, Grep, Web Fetch, Web Search) |
| `evolution` | Agent evolution/self-improvement capabilities (includes `advisor`) |
| `mcp_toolset` | MCP tools connected via declared MCP Servers |

**Built-in toolset tools:**

| Tool | Config Name | Description |
|------|-------------|-------------|
| Bash | `bash` | Execute bash commands in sandbox |
| Read | `read` | Read files in sandbox |
| Write | `write` | Write/overwrite files in sandbox |
| Edit | `edit` | String replacement in files |
| Glob | `glob` | Find files by name |
| Grep | `grep` | Search text by regex |
| Web Fetch | `web_fetch` | Fetch URL content |
| Web Search | `web_search` | Web search (billed per call) |

### 3.2 Enable Built-in Tools

```bash
curl https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "CodeAssistant",
    "model": {"id": "doubao-seed-2-1-pro-260628"},
    "tools": [{"type": "agent_toolset_20260701"}]
  }'
```

### 3.3 Disable Specific Tools

```bash
curl https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "RestrictedAgent",
    "model": {"id": "doubao-seed-2-1-pro-260628"},
    "tools": [{
      "type": "agent_toolset_20260701",
      "configs": [{"name": "bash", "enabled": false}]
    }]
  }'
```

### 3.4 Enable Evolution (Advisor)

```bash
curl https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "SelfEvolvingAgent",
    "model": {"id": "doubao-seed-2-1-pro-260628"},
    "tools": [
      {"type": "agent_toolset_20260701"},
      {"type": "evolution", "configs": [{"name": "advisor", "enabled": true}]}
    ]
  }'
```

---

## 4. Tool Permission Policies

Controls whether tool calls execute automatically or require human confirmation.

### 4.1 Policy Types

| Policy | Description |
|--------|-------------|
| `always_allow` | Auto-execute, no confirmation needed |
| `always_ask` | Pause and wait for confirmation before executing |

**Defaults:**
- `agent_toolset_20260701`: `always_allow`
- `mcp_toolset`: `always_ask`

### 4.2 Set Default Policy for Toolset

```bash
curl https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "CautiousAgent",
    "model": {"id": "doubao-seed-2-1-pro-260628"},
    "tools": [{
      "type": "agent_toolset_20260701",
      "default_config": {
        "permission_policy": {"type": "always_ask"}
      }
    }]
  }'
```

### 4.3 Override Policy for a Single Tool

```bash
curl https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "BashCautiousAgent",
    "model": {"id": "doubao-seed-2-1-pro-260628"},
    "tools": [{
      "type": "agent_toolset_20260701",
      "configs": [{
        "name": "bash",
        "permission_policy": {"type": "always_ask"}
      }]
    }]
  }'
```

---

## 5. MCP (Model Context Protocol)

Connect third-party systems as tools via MCP Servers.

### 5.1 Declare MCP Server on Agent

Each MCP server needs: `type` (always `url`), `name` (unique within agent), `url` (SSE endpoint).

Every MCP server entry must have a corresponding `mcp_toolset` entry in `tools`.

```bash
curl https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "GitHubAssistant",
    "model": {"id": "doubao-seed-2-1-pro-260628"},
    "mcp_servers": [{
      "type": "url",
      "name": "github",
      "url": "https://mcp.example.com/github"
    }],
    "tools": [
      {"type": "agent_toolset_20260701"},
      {"type": "mcp_toolset", "mcp_server_name": "github"}
    ]
  }'
```

### 5.2 Control Which MCP Tools Are Available

Use `default_config` and `configs` to selectively enable/disable MCP tools.

```bash
curl https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "SelectiveGitHubAgent",
    "model": {"id": "doubao-seed-2-1-pro-260628"},
    "mcp_servers": [{
      "type": "url", "name": "github",
      "url": "https://mcp.example.com/github"
    }],
    "tools": [{
      "type": "mcp_toolset",
      "mcp_server_name": "github",
      "default_config": {"enabled": false},
      "configs": [
        {"name": "list_issues", "enabled": true},
        {"name": "get_issue", "enabled": true},
        {"name": "add_issue_comment", "enabled": true}
      ]
    }]
  }'
```

### 5.3 Inject MCP Credentials in Session

Credentials are injected at Session creation via `vault_ids`, not during Agent definition.

```bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent": "agent-20260701120000-abcde",
    "environment_id": "env-20260701120000-fghij",
    "vault_ids": ["vault-20260701120000-xxxxx"]
  }'
```

---

## 6. Multi-Agent (Coordinator Pattern)

Allows an Agent (coordinator) to delegate tasks to other Agents (sub-agents).

### 6.1 Configuration

```bash
curl https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "engineering-lead",
    "model": {"id": "doubao-seed-2-1-pro-260628"},
    "system": "You are the tech lead. Delegate tasks to team members as needed.",
    "multiagent": {
      "type": "coordinator",
      "agents": [
        {"type": "agent", "id": "agent-20260702070101-xxxxx"},
        {"type": "agent", "id": "agent-20260702070202-yyyyy"},
        {"type": "self"}
      ]
    }
  }'
```

**Notes:**
- `type` must be `coordinator`
- `agents[]` can reference other Agents by ID or `"self"` (the coordinator also handles tasks)
- An Agent configured with `multiagent` cannot itself be a sub-agent of another (prevents circular delegation)

---

## 7. Environment Management

### 7.1 Create Environment

`POST /api/v3/environments`

```bash
curl https://ark.cn-beijing.volces.com/api/v3/environments \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-agent-env",
    "description": "Environment for development agent",
    "config": {
      "type": "cloud",
      "networking": {"type": "unrestricted"},
      "packages": {
        "pip": ["pandas==2.2.0"],
        "apt": ["curl", "ffmpeg"]
      },
      "env": {
        "MY_KEY_0": "value_0"
      }
    }
  }'
```

**Supported Package Managers:**

| Field | Manager | Example |
|-------|---------|---------|
| `apt` | System packages | `"ffmpeg"` |
| `cargo` | Rust (cargo) | `"ripgrep@14.0.0"` |
| `gem` | Ruby (gem) | `"rails:7.1.0"` |
| `go` | Go modules | `"golang.org/x/tools/cmd/goimports@latest"` |
| `npm` | Node.js (npm) | `"express@4.18.0"` |
| `pip` | Python (pip) | `"pandas==2.2.0"` |

### 7.2 Sandbox Specifications

| Property | Value |
|----------|-------|
| OS | Ubuntu 22.04 LTS |
| Architecture | x86_64 (amd64) |
| Memory | 4 GB |
| Disk | 10 GB |
| Network | Enabled by default |

**Pre-installed Languages:** Python 3.12+, Node.js 20+, Go 1.25+, Rust 1.77+, Java 21+, Ruby 3.3+, PHP 8.3+, C/C++ (GCC 13+)

**Pre-installed Tools:** git, curl, wget, jq, tar, zip, unzip, ssh, scp, tmux, make, cmake, ripgrep, tree, htop, vim, nano, sed, awk, grep, diff, patch, SQLite, psql, redis-cli

---

## 8. Session Management

### 8.1 Create Session

`POST /api/v3/sessions`

Sessions start in `idle` state. They don't begin work until the first event is sent.

```bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent": "agent-20260701120000-abcde",
    "environment_id": "env-20260701120000-fghij"
  }'
```

**Response:**
```json
{
  "id": "sesn-20260701120100-klmno",
  "type": "session",
  "status": "idle",
  "environment_id": "env-20260701120000-fghij",
  "agent": {"id": "agent-20260701120000-abcde", "type": "agent", "version": 3},
  "created_at": "2026-06-29T10:00:00Z",
  "updated_at": "2026-06-29T10:00:00Z"
}
```

### 8.2 Session States

| State | Description |
|-------|-------------|
| `idle` | Waiting for input (user message or tool confirmation) |
| `running` | Agent is actively executing |
| `rescheduled` | Temporary error, system auto-retrying |
| `terminated` | Unrecoverable error, session ended |

### 8.3 Send Events to Session

`POST /api/v3/sessions/{session_id}/events`

Send a `user.message` event to start the Agent working:

```bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions/sesn-20260701120100-klmno/events \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "user.message",
    "content": [{"type": "text", "text": "Summarize the latest tech news"}]
  }'
```

### 8.4 Get Session Details

`GET /api/v3/sessions/{session_id}`

Returns session status, usage statistics, and configuration snapshot.

```bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions/sesn-20260701120100-klmno \
  -H "Authorization: Bearer $ARK_API_KEY"
```

### 8.5 List Sessions

`GET /api/v3/sessions`

### 8.6 Delete Session

`DELETE /api/v3/sessions/{session_id}`

Permanently deletes the session and its event history.

---

## 9. Vaults (Credentials Management)

Vaults securely store credentials (API keys, tokens, etc.) that are injected into Sessions.

### 9.1 Create Vault

`POST /api/v3/vaults`

```bash
curl https://ark.cn-beijing.volces.com/api/v3/vaults \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "github-token",
    "description": "GitHub personal access token",
    "config": {
      "type": "mcp_credentials",
      "mcp_server_name": "github",
      "credentials": {
        "token": "ghp_xxxxxxxxxxxx"
      }
    }
  }'
```

### 9.2 Use Vault in Session

Reference vault IDs when creating a Session:

```bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent": "agent-20260701120000-abcde",
    "environment_id": "env-20260701120000-fghij",
    "vault_ids": ["vault-20260701120000-xxxxx"]
  }'
```

---

## 10. Context Management

### 10.1 Agent Memory & Resources

Agents have access to a sandbox directory structure:

```
/
├── workspace/          # Working directory (read/write)
├── mnt/
│   ├── knowledge/      # Read-only knowledge base
│   ├── memory/         # Read-only memory/skills
│   ├── resources/      # Read-only uploaded files
│   ├── outputs/        # Read/write output directory
│   └── storage/        # Read/write persistent storage
└── tmp/                # Temporary directory
```

### 10.2 Upload Files to Session

Files can be uploaded as resources and referenced in events:

```bash
curl https://ark.cn-beijing.volces.com/api/v3/sessions/{session_id}/resources \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -F "file=@./document.pdf"
```

---

## 11. Complete Workflow Examples

### 11.1 Agent Lifecycle: Create → Run → Monitor

```bash
# 1. Create an Agent
AGENT=$(curl -s https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "DevAgent",
    "model": {"id": "doubao-seed-2-1-pro-260628"},
    "system": "You are a helpful development assistant.",
    "tools": [{"type": "agent_toolset_20260701"}]
  }')
AGENT_ID=$(echo "$AGENT" | jq -r '.id')
echo "Agent ID: $AGENT_ID"

# 2. Create an Environment
ENV=$(curl -s https://ark.cn-beijing.volces.com/api/v3/environments \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "dev-env",
    "config": {
      "type": "cloud",
      "networking": {"type": "unrestricted"},
      "packages": {"pip": ["pandas"]}
    }
  }')
ENV_ID=$(echo "$ENV" | jq -r '.id')
echo "Environment ID: $ENV_ID"

# 3. Create a Session
SESSION=$(curl -s https://ark.cn-beijing.volces.com/api/v3/sessions \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"agent\": \"$AGENT_ID\", \"environment_id\": \"$ENV_ID\"}")
SESSION_ID=$(echo "$SESSION" | jq -r '.id')
echo "Session ID: $SESSION_ID"

# 4. Send a task event
curl -s https://ark.cn-beijing.volces.com/api/v3/sessions/$SESSION_ID/events \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "user.message",
    "content": [{"type": "text", "text": "Create a Python script to analyze the data."}]
  }'

# 5. Check session status
curl -s https://ark.cn-beijing.volces.com/api/v3/sessions/$SESSION_ID \
  -H "Authorization: Bearer $ARK_API_KEY"
```

### 11.2 Agent with MCP + Vault

```bash
# 1. Create Agent with MCP
curl -s https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "GitHubIssueAgent",
    "model": {"id": "doubao-seed-2-1-pro-260628"},
    "system": "You help manage GitHub issues.",
    "mcp_servers": [{
      "type": "url", "name": "github",
      "url": "https://mcp.example.com/github"
    }],
    "tools": [
      {"type": "agent_toolset_20260701"},
      {"type": "mcp_toolset", "mcp_server_name": "github"}
    ]
  }' | jq '.id'

# 2. Create a Vault for GitHub credentials
curl -s https://ark.cn-beijing.volces.com/api/v3/vaults \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "github-creds",
    "config": {
      "type": "mcp_credentials",
      "mcp_server_name": "github",
      "credentials": {"token": "'$GITHUB_TOKEN'"}
    }
  }' | jq '.id'

# 3. Create Session with vault reference
curl -s https://ark.cn-beijing.volces.com/api/v3/sessions \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent": "agent-xxxxx",
    "environment_id": "env-xxxxx",
    "vault_ids": ["vault-xxxxx"]
  }' | jq '.id'
```

### 11.3 Multi-Agent Coordination

```bash
# Create sub-agents first
SUB_AGENT_1=$(curl -s https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "FrontendDev",
    "model": {"id": "doubao-seed-2-1-pro-260628"},
    "system": "You are a frontend developer specialist.",
    "tools": [{"type": "agent_toolset_20260701"}]
  }' | jq -r '.id')

SUB_AGENT_2=$(curl -s https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "BackendDev",
    "model": {"id": "doubao-seed-2-1-pro-260628"},
    "system": "You are a backend developer specialist.",
    "tools": [{"type": "agent_toolset_20260701"}]
  }' | jq -r '.id')

# Create coordinator agent
curl -s https://ark.cn-beijing.volces.com/api/v3/agents \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "TechLead",
    "model": {"id": "doubao-seed-2-1-pro-260628"},
    "system": "You are the tech lead. Delegate frontend tasks to the frontend dev and backend tasks to the backend dev.",
    "multiagent": {
      "type": "coordinator",
      "agents": [
        {"type": "agent", "id": "'$SUB_AGENT_1'"},
        {"type": "agent", "id": "'$SUB_AGENT_2'"},
        {"type": "self"}
      ]
    }
  }' | jq '.id'
```

---

## 12. Design Guidelines

- Put **stable capabilities** in Agent definitions; put **one-shot tasks** in Session events.
- `system` should define the role, constraints, and long-term rules — **not** the current task.
- For external system integration, prefer **MCP** over custom tools.
- For reusable domain knowledge or execution standards, prefer **Skills** over cramming long instructions into `system`.
- Use **permission policies** to gate risky tools (`bash`, `web_search`) with `always_ask`.
- Agent names are **immutable after creation** for version tracking consistency.
- Sessions are **immutable once running** — you cannot modify session fields mid-execution.

## 13. Related Documentation

| Resource | Description |
|----------|-------------|
| [Agent Definition](https://www.volcengine.com/docs/82379/2553716) | Create and manage Agents |
| [Skills](https://www.volcengine.com/docs/82379/2553717) | SkillHub and custom Skills |
| [MCP](https://www.volcengine.com/docs/82379/2553718) | Model Context Protocol integration |
| [Tools](https://www.volcengine.com/docs/82379/2553719) | Built-in tools and configuration |
| [Tool Permission Policy](https://www.volcengine.com/docs/82379/2553720) | Access control for tools |
| [Environment Setup](https://www.volcengine.com/docs/82379/2553721) | Sandbox environment configuration |
| [Sandbox Details](https://www.volcengine.com/docs/82379/2553722) | Pre-installed software and specs |
| [Session Startup](https://www.volcengine.com/docs/82379/2553723) | Create and run Sessions |
| [Session Lifecycle](https://www.volcengine.com/docs/82379/2553724) | States, retrieval, deletion |
| [Multi Agent](https://www.volcengine.com/docs/82379/2553730) | Coordinator pattern delegation |
| [Create Agent API](https://www.volcengine.com/docs/82379/2555910) | Full API reference for Agent creation |
| [Base URL & Auth](https://www.volcengine.com/docs/82379/1298459) | Authentication details |