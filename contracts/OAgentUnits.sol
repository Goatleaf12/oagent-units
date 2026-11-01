// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import "@openzeppelin/contracts/token/common/ERC2981.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/// @title OAGENT Units — proof-of-work pixel sea-animal NFTs on BNB Chain.
/// @notice Mint works like the classic PoW drops: each wallet gets a challenge
///         (challengeFor), and mint(nonce) succeeds only when
///         keccak256(challenge || nonce) has at least MIN_BITS leading zero
///         bits. The STRONGER the proof, the rarer the unit — rarity is mined,
///         not drawn: 20-21 bits Common, 22-23 Uncommon, 24-26 Epic, 27+
///         Legendary. Every unit is UNIQUE: its metadata (image + traits)
///         is rendered off-chain from its own mint proof and served from
///         the collection base URI — a live metadata service while the
///         mint runs, frozen to a pinned IPFS folder when minting ends.
///         tokenURI(tokenId) is baseURI + tokenId (see _baseURI).
///         The mint price escalates per wallet: 0.0001 BNB for the first
///         unit, 0.0002 for the second, 0.0003 for the third (see
///         mintPrice). Fees go to the contract and can be withdrawn by the
///         owner; gas is paid by the minter to the network, as usual.
contract OAgentUnits is ERC721, ERC2981, Ownable {
    uint256 public constant MAX_SUPPLY = 3333;
    uint256 public constant MINT_FEE = 0.0001 ether;
    uint256 public constant MAX_PER_WALLET = 3;
    uint256 public constant MIN_BITS = 20;

    uint256 public totalMinted;
    // The mint starts CLOSED. The owner opens it with setMintOpen(true)
    // only after the metadata base URI points at the live service, so no
    // unit can be minted against a placeholder baseURI.
    bool public mintOpen = false;

    // PLACEHOLDER — not a real CID. Point this at the live metadata
    // service with setBaseURI() before the mint opens; it is repointed
    // once to the final pinned IPFS folder when minting ends.
    // tokenURI = this + tokenId.
    string private _baseTokenURI = "ipfs://OAGENT_METADATA_BASE/";

    mapping(uint256 => uint8) private _tiers; // 0 Common, 1 Uncommon, 2 Epic, 3 Legendary
    mapping(uint256 => uint256) private _proofBits; // leading zero bits of the mint proof
    mapping(address => uint256) public mintedBy;

    event UnitMinted(address indexed minter, uint256 indexed tokenId, uint8 tier, uint256 proofBits);
    event MintOpenChanged(bool open);
    event BaseURIChanged(string newBaseURI);

    /// @param initialOwner The project wallet. Deployment goes through the
    ///         deterministic CREATE2 deployment proxy (0x4e59…4956C),
    ///         because the agentic wallet cannot send a bare creation
    ///         transaction — so the owner and the royalty receiver are set
    ///         explicitly here instead of relying on msg.sender, which
    ///         during a proxy deployment would be the proxy itself.
    constructor(address initialOwner) ERC721("OAGENT Units", "OUNIT") Ownable(initialOwner) {
        _setDefaultRoyalty(initialOwner, 500); // 5% secondary royalty to the owner wallet; no setter exists, so this can never change
    }

    // ---------------- mint (proof-of-work) ----------------

    /// @notice The challenge a wallet must grind against right now. It rotates
    ///         after every successful mint because it binds the mint count.
    function challengeFor(address wallet) public view returns (bytes32) {
        return keccak256(abi.encodePacked(address(this), wallet, mintedBy[wallet]));
    }

    /// @notice Price of the NEXT unit for a wallet: the base fee times how
    ///         many units that wallet will hold after this mint — 1st unit
    ///         0.0001 BNB, 2nd 0.0002, 3rd 0.0003. Minting more costs more.
    function mintPrice(address wallet) public view returns (uint256) {
        return MINT_FEE * (mintedBy[wallet] + 1);
    }

    /// @notice Mint one unit by presenting a nonce whose
    ///         keccak256(challengeFor(you) || nonce) has >= MIN_BITS leading
    ///         zero bits. Tier is decided by the proof's strength.
    function mint(uint256 nonce) external payable {
        require(mintOpen, "mint is closed");
        require(mintedBy[msg.sender] < MAX_PER_WALLET, "wallet limit");
        require(totalMinted < MAX_SUPPLY, "sold out");
        require(msg.value == mintPrice(msg.sender), "wrong fee");

        bytes32 hash = keccak256(abi.encodePacked(challengeFor(msg.sender), nonce));
        uint256 bits = _leadingZeroBits(hash);
        require(bits >= MIN_BITS, "proof too weak");

        uint256 tokenId = ++totalMinted;
        uint8 tier = tierForBits(bits);
        _tiers[tokenId] = tier;
        _proofBits[tokenId] = bits;
        mintedBy[msg.sender] += 1;
        _safeMint(msg.sender, tokenId);
        emit UnitMinted(msg.sender, tokenId, tier, bits);
    }

    /// @dev Tier bands by proof strength (luck = bits - MIN_BITS):
    ///      Common luck 0-1, Uncommon 2-3, Epic 4-6, Legendary 7+.
    function tierForBits(uint256 bits) public pure returns (uint8) {
        if (bits >= MIN_BITS + 7) return 3;
        if (bits >= MIN_BITS + 4) return 2;
        if (bits >= MIN_BITS + 2) return 1;
        return 0;
    }

    function _leadingZeroBits(bytes32 h) internal pure returns (uint256 n) {
        uint256 x = uint256(h);
        if (x == 0) return 256;
        while (x < (1 << 255)) {
            x <<= 1;
            n++;
        }
    }

    function proofBitsOf(uint256 tokenId) public view returns (uint256) {
        _requireOwned(tokenId);
        return _proofBits[tokenId];
    }

    // ---------------- views ----------------

    function tierOf(uint256 tokenId) public view returns (uint8) {
        _requireOwned(tokenId);
        return _tiers[tokenId];
    }

    function tierName(uint8 tier) public pure returns (string memory) {
        if (tier == 0) return "Common";
        if (tier == 1) return "Uncommon";
        if (tier == 2) return "Epic";
        return "Legendary";
    }

    // ---------------- metadata ----------------

    /// @notice The base URI every tokenURI is built from. The standard
    ///         ERC721 tokenURI(tokenId) returns baseURI + tokenId and
    ///         reverts for tokens that do not exist.
    function baseURI() public view returns (string memory) {
        return _baseTokenURI;
    }

    function _baseURI() internal view override returns (string memory) {
        return _baseTokenURI;
    }

    // ---------------- owner ----------------

    function setMintOpen(bool open) external onlyOwner {
        mintOpen = open;
        emit MintOpenChanged(open);
    }

    /// @notice Set the metadata base URI (tokenURI = baseURI + tokenId).
    ///         Owner-only. Launch plan: set to the live metadata service
    ///         before the mint opens, then repointed once to the final
    ///         pinned IPFS folder when minting ends. The function itself
    ///         is not code-locked; the two-use plan is public in llms.txt.
    function setBaseURI(string calldata newBaseURI) external onlyOwner {
        _baseTokenURI = newBaseURI;
        emit BaseURIChanged(newBaseURI);
    }

    /// @notice Withdraw accumulated mint fees to the owner wallet.
    function withdraw() external onlyOwner {
        uint256 bal = address(this).balance;
        require(bal > 0, "nothing to withdraw");
        (bool ok, ) = payable(owner()).call{value: bal}("");
        require(ok, "withdraw failed");
    }

    function supportsInterface(bytes4 interfaceId)
        public
        view
        override(ERC721, ERC2981)
        returns (bool)
    {
        return super.supportsInterface(interfaceId);
    }
}
