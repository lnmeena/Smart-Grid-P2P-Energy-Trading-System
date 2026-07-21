// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./UserModule.sol";

abstract contract TradeModule is UserModule {
    enum TradeStatus {
        IN_PROGRESS,
        ACTIVE,
        COMPLETED,
        CANCELLED,
        FRAUD_REPORTED
    }

    enum OracleResult {
        COMPLETED,
        FAILURE,
        FRAUD
    }

    event Debug(address sender, address engine);

    struct Trade {
        uint256 seller;
        uint256 buyer;
        uint256 energy;
        uint256 price;
        uint256 stake;
        TradeStatus status;
        bool moneyCollected;
        uint256 expiryTime;
    }

    event TradeInitiated(uint256 indexed tradeId);

    event InsufficientFunds(
        uint256 indexed tradeId,
        uint256 indexed meterId,
        string reason
    );

    event TradeCompleted(uint256 indexed tradeId);

    event TradeCancelled(uint256 indexed tradeId);

    event FraudReported(uint256 indexed tradeId);

    mapping(uint256 => Trade) public trades;

    address public pythonEngine;
    address public engineOracle;

    uint256 public constant SLASH_PERCENTAGE = 10;
    uint256 public constant MAX_REPUTATION = 100;
    uint256 public constant MIN_REPUTATION = 0;
    uint256 public constant TRADE_TIMEOUT = 1 hours;

    function setPythonEngine(
        address _pythonEngine
    )
        external
        onlyOwner
    {
        pythonEngine = _pythonEngine;
    }

    function setEngineOracle(
        address _engineOracle
    )
        external
        onlyOwner
    {
        engineOracle = _engineOracle;
    }

    function createTrade(
        uint256 tradeId,
        uint256 _seller,
        uint256 _buyer,
        uint256 _energy,
        uint256 _price,
        uint256 _stake
    )
        external
        nonReentrant
    {

        emit Debug(msg.sender, pythonEngine);

        require(
            msg.sender == pythonEngine,
            "Only python engine can create trades"
        );

        require(
            trades[tradeId].expiryTime == 0,
            "Trade already exists"
        );

        trades[tradeId] = Trade({
            seller: _seller,
            buyer: _buyer,
            energy: _energy,
            price: _price,
            stake: _stake,
            status: TradeStatus.IN_PROGRESS,
            moneyCollected: false,
            expiryTime: block.timestamp + TRADE_TIMEOUT
        });

        if (!_getStakes(tradeId)) {
            trades[tradeId].status = TradeStatus.CANCELLED;

            emit TradeCancelled(tradeId);
            return;
        }

        if (!_getMoneyFromBuyer(tradeId)) {
            modifyBalance(_buyer, _stake, false);
            modifyBalance(_seller, _stake, false);

            trades[tradeId].status = TradeStatus.CANCELLED;

            emit TradeCancelled(tradeId);
            return;
        }

        trades[tradeId].status = TradeStatus.ACTIVE;

        emit TradeInitiated(tradeId);
    }

    function _getStakes(
        uint256 tradeId
    )
        internal
        returns (bool)
    {
        Trade storage currentTrade = trades[tradeId];

        if (
            users[currentTrade.buyer].amountStored <
            currentTrade.stake
        ) {
            emit InsufficientFunds(
                tradeId,
                currentTrade.buyer,
                "Buyer insufficient funds for stake"
            );

            _decreaseReputation(
                currentTrade.buyer,
                10
            );

            return false;
        }

        if (
            users[currentTrade.seller].amountStored <
            currentTrade.stake
        ) {
            emit InsufficientFunds(
                tradeId,
                currentTrade.seller,
                "Seller insufficient funds for stake"
            );

            _decreaseReputation(
                currentTrade.seller,
                10
            );

            return false;
        }

        modifyBalance(
            currentTrade.buyer,
            currentTrade.stake,
            true
        );

        modifyBalance(
            currentTrade.seller,
            currentTrade.stake,
            true
        );

        return true;
    }

    function _getMoneyFromBuyer(
        uint256 tradeId
    )
        internal
        returns (bool)
    {
        Trade storage currentTrade = trades[tradeId];

        if (
            users[currentTrade.buyer].amountStored <
            currentTrade.price
        ) {
            emit InsufficientFunds(
                tradeId,
                currentTrade.buyer,
                "Buyer insufficient funds for price"
            );

            _decreaseReputation(
                currentTrade.buyer,
                10
            );

            return false;
        }

        modifyBalance(
            currentTrade.buyer,
            currentTrade.price,
            true
        );

        currentTrade.moneyCollected = true;

        return true;
    }

    function checkOracle(
        uint256 tradeId,
        OracleResult result
    )
        external
        nonReentrant
    {
        Trade storage currentTrade = trades[tradeId];

        require(
            currentTrade.expiryTime != 0,
            "Trade does not exist"
        );

        require(
            msg.sender == engineOracle,
            "Unauthorized"
        );

        require(
            currentTrade.status ==
            TradeStatus.ACTIVE,
            "Trade is not active"
        );

        require(
            block.timestamp <=
            currentTrade.expiryTime,
            "Trade has expired"
        );

        if (result == OracleResult.COMPLETED) {
            currentTrade.status =
                TradeStatus.COMPLETED;

            _settleTradeSuccess(tradeId);

            emit TradeCompleted(tradeId);
        }
        else if (result == OracleResult.FAILURE) {
            currentTrade.status =
                TradeStatus.CANCELLED;

            _refundAll(tradeId);

            emit TradeCancelled(tradeId);
        }
        else if (result == OracleResult.FRAUD) {
            currentTrade.status =
                TradeStatus.FRAUD_REPORTED;

            _penalizeSellerFraud(tradeId);

            emit FraudReported(tradeId);
        }
    }

    function expireTrade(
        uint256 tradeId
    )
        external
        nonReentrant
    {
        Trade storage currentTrade = trades[tradeId];

        require(
            currentTrade.expiryTime != 0,
            "Trade does not exist"
        );

        require(
            currentTrade.status ==
                TradeStatus.ACTIVE ||
            currentTrade.status ==
                TradeStatus.IN_PROGRESS,
            "Trade is already finalized"
        );

        require(
            block.timestamp >
            currentTrade.expiryTime,
            "Trade has not expired yet"
        );

        currentTrade.status =
            TradeStatus.CANCELLED;

        _refundAll(tradeId);

        emit TradeCancelled(tradeId);
    }

    function _settleTradeSuccess(
        uint256 tradeId
    )
        internal
    {
        Trade storage currentTrade = trades[tradeId];

        modifyBalance(
            currentTrade.seller,
            currentTrade.price,
            false
        );

        modifyBalance(
            currentTrade.buyer,
            currentTrade.stake,
            false
        );

        modifyBalance(
            currentTrade.seller,
            currentTrade.stake,
            false
        );

        _increaseReputation(
            currentTrade.buyer,
            10
        );

        _increaseReputation(
            currentTrade.seller,
            10
        );
    }

    function _refundAll(
        uint256 tradeId
    )
        internal
    {
        Trade storage currentTrade = trades[tradeId];

        if (currentTrade.moneyCollected) {
            modifyBalance(
                currentTrade.buyer,
                currentTrade.price,
                false
            );
        }

        modifyBalance(
            currentTrade.buyer,
            currentTrade.stake,
            false
        );

        modifyBalance(
            currentTrade.seller,
            currentTrade.stake,
            false
        );
    }

    function _penalizeSellerFraud(
        uint256 tradeId
    )
        internal
    {
        Trade storage currentTrade = trades[tradeId];

        uint256 currentRep =
            users[currentTrade.seller]
                .reputation;

        uint256 penalty =
            currentRep < 100 ? 50 : 20;

        _decreaseReputation(
            currentTrade.seller,
            penalty
        );

        modifyBalance(
            currentTrade.buyer,
            currentTrade.price,
            false
        );

        modifyBalance(
            currentTrade.buyer,
            currentTrade.stake,
            false
        );

        uint256 slashAmount =
            (currentTrade.stake *
                SLASH_PERCENTAGE) / 100;

        if (
            currentTrade.stake >
            slashAmount
        ) {
            modifyBalance(
                currentTrade.seller,
                currentTrade.stake -
                    slashAmount,
                false
            );

            modifyBalance(
                currentTrade.buyer,
                slashAmount,
                false
            );
        }
        else {
            modifyBalance(
                currentTrade.buyer,
                currentTrade.stake,
                false
            );
        }
    }

    function _increaseReputation(
        uint256 meterId,
        uint256 amount
    )
        internal
    {
        if (
            users[meterId].reputation +
                amount >
            MAX_REPUTATION
        ) {
            users[meterId]
                .reputation = MAX_REPUTATION;
        } else {
            users[meterId]
                .reputation += amount;
        }
    }

    function _decreaseReputation(
        uint256 meterId,
        uint256 amount
    )
        internal
    {
        if (
            users[meterId].reputation <=
            MIN_REPUTATION + amount
        ) {
            users[meterId]
                .reputation = MIN_REPUTATION;
        } else {
            users[meterId]
                .reputation -= amount;
        }
    }

    function getTradeStatus(
        uint256 tradeId
    )
        external
        view
        returns (uint8)
    {
        require(
            trades[tradeId].expiryTime != 0,
            "Trade does not exist"
        );

        return uint8(
            trades[tradeId].status
        );
    }

    function getTrade(
        uint256 tradeId
    )
        external
        view
        returns (
            uint256 seller,
            uint256 buyer,
            uint256 energy,
            uint256 price,
            uint256 stake,
            uint8 status,
            bool moneyCollected,
            uint256 expiryTime
        )
    {
        Trade storage t =
            trades[tradeId];

        return (
            t.seller,
            t.buyer,
            t.energy,
            t.price,
            t.stake,
            uint8(t.status),
            t.moneyCollected,
            t.expiryTime
        );
    }
}
