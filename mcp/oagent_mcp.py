#!/usr/bin/env python3
"""OAGENT Units — reference MCP server.

A dependency-free Python 3 MCP (Model Context Protocol) server over stdio
that helps an AI agent inspect the OAGENT Units collection and prepare a
proof-of-work mint on BNB Smart Chain (chainId 56).

Tools:
  - collection_info : live on-chain reads (totalMinted, mintOpen,
                      MAX_SUPPLY, contract BNB balance).
  - unit_info       : proofBitsOf / tierOf for a token id, plus its
                      metadata URL.
  - grind_proof     : compute the wallet's mint challenge exactly as the
                      contract does and grind a nonce whose proof hash
                      has at least `target_bits` leading zero bits.
  - mint_calldata   : grind, then return the UNSIGNED mint transaction
                      parameters {to, value, data}.

This server NEVER holds keys and NEVER signs or sends transactions. It
only reads public chain state and performs local hash grinding. The
calling agent signs and submits any transaction with its own wallet.

Contract: 0x3eaEaBAE0E3d5E85AFF0503B1Ec98880f4a5B374 (BNB Smart Chain)
Spec:     https://oagent.pages.dev/llms.txt
"""

import json
import sys
import urllib.request

# --------------------------------------------------------------------------
# Constants (from contracts/OAgentUnits.sol — constants in the source)
# --------------------------------------------------------------------------

CONTRACT_ADDRESS = "0x3eaEaBAE0E3d5E85AFF0503B1Ec98880f4a5B374"
CHAIN_ID = 56
MINT_FEE_WEI = 100_000_000_000_000  # MINT_FEE = 0.0001 ether
MAX_PER_WALLET = 3
MIN_BITS = 20
METADATA_BASE = "https://oagent-meta.oagenthq.workers.dev/"

RPC_URLS = [
    "https://bsc-dataseed.binance.org",
    "https://bsc-rpc.publicnode.com",
    "https://bsc-dataseed1.defibit.io",
]
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

TIERS = {
    0: ("Common", "Shrimp"),
    1: ("Uncommon", "Fish"),
    2: ("Epic", "Shark"),
    3: ("Legendary", "Whale"),
}

SERVER_NAME = "oagent-units"
SERVER_VERSION = "1.0.0"
DEFAULT_PROTOCOL_VERSION = "2024-11-05"

# --------------------------------------------------------------------------
# Keccak-256 (Ethereum variant — original Keccak padding 0x01, NOT the
# NIST SHA3-256 padding 0x06 that hashlib.sha3_256 uses). Pure Python.
#
# Self-test: keccak256(b"") must equal
#   c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470
# --------------------------------------------------------------------------

_MASK64 = (1 << 64) - 1

_KECCAK_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
    0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]

# Rotation offsets r[x][y] for the rho step.
_KECCAK_ROT = [
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56],
    [27, 20, 39, 8, 14],
]


def _rotl64(value, shift):
    if shift == 0:
        return value & _MASK64
    return ((value << shift) | (value >> (64 - shift))) & _MASK64


def _keccak_f1600(state):
    """One Keccak-f[1600] permutation. `state` is a list of 25 lanes,
    indexed x + 5*y, mutated in place."""
    for rc in _KECCAK_RC:
        # theta
        c = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15]
             ^ state[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl64(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            dx = d[x]
            for y in range(5):
                state[x + 5 * y] ^= dx
        # rho + pi
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rotl64(
                    state[x + 5 * y], _KECCAK_ROT[x][y])
        # chi
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] = (
                    b[x + 5 * y]
                    ^ ((~b[(x + 1) % 5 + 5 * y]) & b[(x + 2) % 5 + 5 * y])
                )
        # iota
        state[0] ^= rc


def keccak256(data: bytes) -> bytes:
    """Ethereum Keccak-256 of `data` (rate 136 bytes, capacity 512)."""
    rate = 136
    padded = bytearray(data)
    padded.append(0x01)                      # Keccak domain suffix bit
    while len(padded) % rate != 0:
        padded.append(0x00)
    padded[-1] |= 0x80                       # final pad10*1 bit
    state = [0] * 25
    for offset in range(0, len(padded), rate):
        block = padded[offset:offset + rate]
        for lane in range(rate // 8):
            state[lane] ^= int.from_bytes(
                block[lane * 8:(lane + 1) * 8], "little")
        _keccak_f1600(state)
    out = bytearray()
    for lane in range(4):                    # 4 lanes = 32 bytes
        out += state[lane].to_bytes(8, "little")
    return bytes(out[:32])


assert keccak256(b"").hex() == (
    "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
), "Keccak-256 self-test failed"


def _selector(signature: str) -> bytes:
    return keccak256(signature.encode("ascii"))[:4]


def leading_zero_bits(digest: bytes) -> int:
    """Leading zero bits of a 32-byte hash, exactly as the contract's
    _leadingZeroBits computes them."""
    value = int.from_bytes(digest, "big")
    if value == 0:
        return 256
    return 256 - value.bit_length()


# --------------------------------------------------------------------------
# Minimal ABI helpers
# --------------------------------------------------------------------------

def _encode_uint256(value: int) -> bytes:
    return value.to_bytes(32, "big")


def _encode_address(address: str) -> bytes:
    return bytes.fromhex(address[2:].lower()).rjust(32, b"\x00")


def _parse_address(address):
    if (not isinstance(address, str) or len(address) != 42
            or not address.startswith("0x")):
        raise ValueError("wallet must be a 0x-prefixed 20-byte address")
    try:
        return bytes.fromhex(address[2:])
    except ValueError:
        raise ValueError("wallet must be a 0x-prefixed 20-byte address")


# --------------------------------------------------------------------------
# Public BSC RPC reads (eth_call / eth_getBalance, first healthy endpoint)
# --------------------------------------------------------------------------

def _rpc(method, params):
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    ).encode("utf-8")
    last_error = None
    for url in RPC_URLS:
        try:
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json",
                         "User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            if "error" in body:
                raise RuntimeError("RPC error: %s" % body["error"])
            return body["result"]
        except Exception as exc:  # try the next endpoint
            last_error = exc
    raise RuntimeError("all BSC RPC endpoints failed: %s" % last_error)


def _eth_call(data: bytes) -> bytes:
    result = _rpc("eth_call", [{"to": CONTRACT_ADDRESS,
                                "data": "0x" + data.hex()}, "latest"])
    return bytes.fromhex(result[2:])


def _call_uint(signature, args=b""):
    return int.from_bytes(_eth_call(_selector(signature) + args), "big")


# --------------------------------------------------------------------------
# Proof-of-work grinding (mirrors the contract exactly)
#
#   challengeFor(wallet) = keccak256(abi.encodePacked(
#       address(this), wallet, mintedBy[wallet]))
#     -> 20-byte contract | 20-byte wallet | 32-byte big-endian count
#   proof hash = keccak256(abi.encodePacked(challenge, nonce))
#     -> 32-byte challenge | 32-byte big-endian nonce
# --------------------------------------------------------------------------

def compute_challenge(wallet: str, minted_by: int) -> bytes:
    wallet_bytes = _parse_address(wallet)
    contract_bytes = bytes.fromhex(CONTRACT_ADDRESS[2:])
    preimage = contract_bytes + wallet_bytes + _encode_uint256(minted_by)
    return keccak256(preimage)


def grind(wallet: str, minted_by: int, target_bits: int):
    """Grind a nonce whose proof hash has >= target_bits leading zero
    bits. The found proof is re-verified before returning."""
    if not isinstance(minted_by, int) or minted_by < 0:
        raise ValueError("minted_by must be a non-negative integer")
    if not isinstance(target_bits, int) or not (1 <= target_bits <= 256):
        raise ValueError("target_bits must be an integer in [1, 256]")
    challenge = compute_challenge(wallet, minted_by)
    nonce = 0
    while True:
        digest = keccak256(challenge + _encode_uint256(nonce))
        bits = leading_zero_bits(digest)
        if bits >= target_bits:
            break
        nonce += 1
    # Mandatory re-verification of the found proof before returning it.
    check = keccak256(challenge + _encode_uint256(nonce))
    check_bits = leading_zero_bits(check)
    if check != digest or check_bits < target_bits:
        raise RuntimeError("internal error: ground proof failed re-check")
    tier = 0
    if check_bits >= MIN_BITS + 7:
        tier = 3
    elif check_bits >= MIN_BITS + 4:
        tier = 2
    elif check_bits >= MIN_BITS + 2:
        tier = 1
    return {
        "wallet": wallet,
        "minted_by": minted_by,
        "challenge": "0x" + challenge.hex(),
        "nonce": nonce,
        "proof_hash": "0x" + check.hex(),
        "proof_bits": check_bits,
        "target_bits": target_bits,
        "tier_if_minted_now": {
            "tier": tier,
            "name": TIERS[tier][0],
            "species": TIERS[tier][1],
        },
        "note": (
            "The challenge binds the wallet's CURRENT mintedBy count and "
            "rotates after every successful mint — re-read mintedBy and "
            "re-grind if it changed since this proof was ground."
        ),
    }


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------

def tool_collection_info(_args):
    total_minted = _call_uint("totalMinted()")
    max_supply = _call_uint("MAX_SUPPLY()")
    mint_open = _call_uint("mintOpen()") != 0
    balance_hex = _rpc("eth_getBalance", [CONTRACT_ADDRESS, "latest"])
    balance_wei = int(balance_hex, 16)
    return {
        "name": "OAGENT Units",
        "symbol": "OUNIT",
        "contract": CONTRACT_ADDRESS,
        "chain": "BNB Smart Chain",
        "chain_id": CHAIN_ID,
        "total_minted": total_minted,
        "max_supply": max_supply,
        "remaining_supply": max_supply - total_minted,
        "mint_open": mint_open,
        "contract_balance_wei": str(balance_wei),
        "contract_balance_bnb": balance_wei / 1e18,
        "min_proof_bits": MIN_BITS,
        "max_per_wallet": MAX_PER_WALLET,
        "note": (
            "mint_open is read live from the contract and is the only "
            "authority on whether minting is open; it can change."
        ),
    }


def tool_unit_info(args):
    token_id = args.get("token_id")
    if not isinstance(token_id, int) or token_id < 1:
        raise ValueError("token_id must be a positive integer")
    arg = _encode_uint256(token_id)
    proof_bits = _call_uint("proofBitsOf(uint256)", arg)
    tier = _call_uint("tierOf(uint256)", arg)
    name, species = TIERS[tier]
    return {
        "token_id": token_id,
        "proof_bits": proof_bits,
        "tier": tier,
        "tier_name": name,
        "species": species,
        "metadata_url": METADATA_BASE + str(token_id),
        "image_url": METADATA_BASE + str(token_id) + ".png",
    }


def tool_grind_proof(args):
    wallet = args.get("wallet")
    _parse_address(wallet)  # validates, raises ValueError with message
    return grind(wallet, args.get("minted_by"), args.get("target_bits"))


def tool_mint_calldata(args):
    wallet = args.get("wallet")
    _parse_address(wallet)
    minted_by = args.get("minted_by")
    target_bits = args.get("target_bits")
    if not isinstance(minted_by, int) or not (0 <= minted_by < MAX_PER_WALLET):
        raise ValueError(
            "minted_by must be 0, 1 or 2 — a wallet that already minted "
            "%d units cannot mint again" % MAX_PER_WALLET)
    if not isinstance(target_bits, int) or target_bits < MIN_BITS:
        raise ValueError(
            "target_bits must be at least %d — the contract rejects "
            "weaker proofs" % MIN_BITS)
    proof = grind(wallet, minted_by, target_bits)
    value_wei = MINT_FEE_WEI * (minted_by + 1)
    calldata = _selector("mint(uint256)") + _encode_uint256(proof["nonce"])
    return {
        "unsigned_transaction": {
            "to": CONTRACT_ADDRESS,
            "chain_id": CHAIN_ID,
            "value_wei": str(value_wei),
            "value_bnb": value_wei / 1e18,
            "data": "0x" + calldata.hex(),
        },
        "proof": proof,
        "warning": (
            "UNSIGNED transaction parameters only. This server never "
            "holds keys and never signs: the calling agent must review "
            "the price (mintPrice for its wallet), sign with its OWN "
            "wallet, and submit. Before submitting, re-read on-chain "
            "mintOpen(), mintedBy(wallet) (must still equal minted_by, "
            "or the challenge — and this calldata — is stale) and "
            "mintPrice(wallet) (must equal value_wei). Show the preview "
            "to your operator and submit only with their approval where "
            "your setup requires it."
        ),
    }


TOOLS = [
    {
        "name": "collection_info",
        "description": (
            "Live on-chain state of the OAGENT Units collection (BNB "
            "Smart Chain): totalMinted, MAX_SUPPLY, remaining supply, "
            "mintOpen, and the contract's BNB balance, read from public "
            "BSC RPC endpoints at call time."
        ),
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_collection_info,
    },
    {
        "name": "unit_info",
        "description": (
            "On-chain proof bits and tier of one minted OAGENT Unit, "
            "plus its metadata and image URLs. Reverts for token ids "
            "that do not exist."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "token_id": {
                    "type": "integer",
                    "description": "Token id (1-based).",
                },
            },
            "required": ["token_id"],
        },
        "handler": tool_unit_info,
    },
    {
        "name": "grind_proof",
        "description": (
            "Compute a wallet's OAGENT mint challenge exactly as the "
            "contract does — keccak256(contract | wallet | mintedBy) — "
            "and grind a nonce whose keccak256(challenge | nonce) has "
            "at least target_bits leading zero bits. Pure-Python "
            "reference grinder: fine for small targets and tests; "
            "production targets (20+ bits, ~1M+ hashes) take minutes "
            "here — agents with native/compiled grinders should prefer "
            "those and may verify the result with this tool's scheme. "
            "The found proof is re-verified before it is returned."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "wallet": {
                    "type": "string",
                    "description": "The minter's 0x wallet address.",
                },
                "minted_by": {
                    "type": "integer",
                    "description": (
                        "The wallet's current lifetime mint count "
                        "(mintedBy on-chain); the challenge binds it."
                    ),
                },
                "target_bits": {
                    "type": "integer",
                    "description": (
                        "Minimum leading zero bits required. The "
                        "contract minimum is 20; 22+ Uncommon, 24+ "
                        "Epic, 27+ Legendary."
                    ),
                },
            },
            "required": ["wallet", "minted_by", "target_bits"],
        },
        "handler": tool_grind_proof,
    },
    {
        "name": "mint_calldata",
        "description": (
            "Grind a proof (target_bits must be >= 20) and return the "
            "UNSIGNED mint transaction parameters {to, value, data} for "
            "mint(uint256 nonce), with the exact price-ladder value for "
            "the given minted_by count. This server never holds keys "
            "and never signs or sends anything — the calling agent "
            "signs with its own wallet, after re-checking mintOpen(), "
            "mintedBy and mintPrice on-chain and showing its operator "
            "a preview."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "wallet": {
                    "type": "string",
                    "description": "The minter's 0x wallet address.",
                },
                "minted_by": {
                    "type": "integer",
                    "description": (
                        "The wallet's current lifetime mint count "
                        "(0, 1 or 2); sets both the challenge and the "
                        "price-ladder value."
                    ),
                },
                "target_bits": {
                    "type": "integer",
                    "description": (
                        "Minimum leading zero bits to grind for "
                        "(>= 20): 20 Common, 22 Uncommon, 24 Epic, "
                        "27 Legendary."
                    ),
                },
            },
            "required": ["wallet", "minted_by", "target_bits"],
        },
        "handler": tool_mint_calldata,
    },
]

_TOOL_MAP = {tool["name"]: tool for tool in TOOLS}


# --------------------------------------------------------------------------
# MCP / JSON-RPC plumbing (stdio, newline-delimited JSON)
# --------------------------------------------------------------------------

def _result_response(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error_response(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": code, "message": message}}


def handle_request(request):
    """Returns a response dict, or None for notifications."""
    method = request.get("method")
    req_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        protocol = params.get("protocolVersion", DEFAULT_PROTOCOL_VERSION)
        return _result_response(req_id, {
            "protocolVersion": protocol,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method == "ping":
        return _result_response(req_id, {})
    if method == "tools/list":
        return _result_response(req_id, {
            "tools": [
                {key: tool[key]
                 for key in ("name", "description", "inputSchema")}
                for tool in TOOLS
            ],
        })
    if method == "tools/call":
        name = params.get("name")
        tool = _TOOL_MAP.get(name)
        if tool is None:
            return _error_response(req_id, -32602, "unknown tool: %s" % name)
        try:
            result = tool["handler"](params.get("arguments") or {})
            text = json.dumps(result, indent=2)
            return _result_response(req_id, {
                "content": [{"type": "text", "text": text}],
                "isError": False,
            })
        except Exception as exc:
            return _result_response(req_id, {
                "content": [{"type": "text",
                             "text": "%s: %s" % (type(exc).__name__, exc)}],
                "isError": True,
            })
    if req_id is None:
        return None  # notification we do not handle: ignore
    return _error_response(req_id, -32601, "method not found: %s" % method)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
