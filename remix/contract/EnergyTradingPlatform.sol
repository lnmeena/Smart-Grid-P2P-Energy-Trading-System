// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./TradeModule.sol";

contract CentralSmartContract is TradeModule {
    constructor() Ownable(msg.sender) {}
}
