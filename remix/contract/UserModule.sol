// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

abstract contract UserModule is ReentrancyGuard, Ownable {
    struct User {
        address wallet;
        uint256 reputation;
        uint256 amountStored;
    }

    mapping(uint256 => User) public users;
    
    address public centralContract;

    modifier onlyAuthorized() {
        require(msg.sender == centralContract || msg.sender == owner(), "Not authorized");
        _;
    }

    function setCentralContract(address _centralContract) external onlyOwner {
        centralContract = _centralContract;
    }

    function registerUser(uint256 meterId) external {
        require(users[meterId].wallet == address(0), "Meter ID already registered");
        
        users[meterId] = User({
            wallet: msg.sender,
            reputation: 50, 
            amountStored: 0
        });
    }

    function payAmount(uint256 meterId) external payable nonReentrant {
        require(users[meterId].wallet == msg.sender, "Only the registered wallet can pay");
        users[meterId].amountStored += msg.value;
    }

    function updateReputation(uint256 meterId, uint256 newReputation) external onlyAuthorized {
        users[meterId].reputation = newReputation;
    }

    function transferAmount(uint256 meterId, uint256 amount) external nonReentrant {
        require(users[meterId].wallet == msg.sender, "Only the registered wallet can withdraw");
        require(users[meterId].amountStored >= amount, "Insufficient balance");
        
        users[meterId].amountStored -= amount;
        (bool success, ) = payable(msg.sender).call{value: amount}("");
        require(success, "Transfer failed");
    }

    function modifyBalance(uint256 meterId, uint256 amount, bool isDeduction) internal {
        if (isDeduction) {
            require(users[meterId].amountStored >= amount, "Insufficient stored amount");
            users[meterId].amountStored -= amount;
        } else {
            users[meterId].amountStored += amount;
        }
    }

    function registerUserWithWallet(
        uint256 meterId,
        address _wallet
    )
        external
        onlyAuthorized
    {
        require(
            users[meterId].wallet == address(0),
            "Meter ID already registered"
        );

        users[meterId] = User({
            wallet: _wallet,
            reputation: 50,
            amountStored: 0
        });
    }

    function isUserRegistered(
        uint256 meterId
    )
        external
        view
        returns (bool)
    {
        return users[meterId].wallet != address(0);
    }

    function getUserWallet(
        uint256 meterId
    )
        external
        view
        returns (address)
    {
        return users[meterId].wallet;
    }

    function getUserReputation(
        uint256 meterId
    )
        external
        view
        returns (uint256)
    {
        return users[meterId].reputation;
    }

    function getUserBalance(
        uint256 meterId
    )
        external
        view
        returns (uint256)
    {
        return users[meterId].amountStored;
    }

    function getUserInfo(
        uint256 meterId
    )
        external
        view
        returns (
            address wallet,
            uint256 reputation,
            uint256 balance
        )
    {
        User storage user = users[meterId];

        return (
            user.wallet,
            user.reputation,
            user.amountStored
        );
    }
}
