# OAGENT Units — MCP server

A dependency-free Python 3 [MCP](https://modelcontextprotocol.io) server
(stdio transport) for the OAGENT Units proof-of-work NFT collection on
BNB Smart Chain. It reads live collection state, looks up minted units,
grinds mint proofs, and builds the unsigned mint transaction.

It **never holds keys and never signs or sends transactions** — the
calling agent signs with its own wallet. See
[`../AGENT_MINT_PROMPT.md`](../AGENT_MINT_PROMPT.md) for the full mint
procedure and safety rules.

## Run

Requires Python 3.8+ and nothing else (standard library only; it ships
its own pure-Python Keccak-256 — Ethereum Keccak, not NIST SHA3).

```bash
python3 mcp/oagent_mcp.py
```

The server speaks newline-delimited JSON-RPC on stdin/stdout, per the
MCP stdio transport.

## Tools

| Tool | Arguments | What it does |
|------|-----------|--------------|
| `collection_info` | — | Live reads: `totalMinted`, `MAX_SUPPLY`, remaining supply, `mintOpen`, contract BNB balance. |
| `unit_info` | `token_id` | `proofBitsOf` + `tierOf` for a minted unit, plus its metadata/image URLs. |
| `grind_proof` | `wallet`, `minted_by`, `target_bits` | Computes the challenge exactly as the contract does and grinds a nonce with at least `target_bits` leading zero bits; the proof is re-verified before return. |
| `mint_calldata` | `wallet`, `minted_by`, `target_bits` (≥ 20) | Grinds, then returns the **unsigned** `{to, value, data}` for `mint(uint256)` at the exact price-ladder value for that mint count. |

Notes:

- The challenge binds the wallet's current `mintedBy` count and rotates
  after every mint — always pass the wallet's current on-chain count,
  and re-check `mintOpen()`, `mintedBy` and `mintPrice` immediately
  before submitting anything built from `mint_calldata`.
- Tier by proof bits: 20–21 Common (Shrimp), 22–23 Uncommon (Fish),
  24–26 Epic (Shark), 27+ Legendary (Whale).
- The pure-Python grinder is a reference implementation. At 20 bits
  (~1M hashes expected) it takes on the order of minutes; production
  agents with compiled grinders should use those and this server for
  reads and verification of the scheme.

## MCP client configuration

Claude Desktop / generic MCP client (`mcpServers`):

```json
{
  "mcpServers": {
    "oagent-units": {
      "command": "python3",
      "args": ["/path/to/oagent-public/mcp/oagent_mcp.py"]
    }
  }
}
```

## Directory manifests

- [`../smithery.yaml`](../smithery.yaml) — Smithery stdio server config.
- [`../server.json`](../server.json) — official MCP registry manifest
  (name + description). A package entry is added when the server is
  published; the registry also requires the `io.github.*` name to match
  the owning GitHub account at publish time.
