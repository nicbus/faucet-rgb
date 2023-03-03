#!/bin/bash
set -eu

COMPOSE="docker compose"
if ! $COMPOSE >/dev/null; then
    echo "could not call docker compose (hint: install docker compose plugin)"
    exit 1
fi

COMPOSE="$COMPOSE -f docker-compose.yml"
TEST_DIR="./tmp"

$COMPOSE down -v
rm -rf $TEST_DIR
mkdir -p $TEST_DIR
$COMPOSE up -d

# wait for bitcoind to be up
until $COMPOSE logs bitcoind |grep 'Bound to'; do
    sleep 1
done

# prepare bitcoin funds
BCLI="$COMPOSE exec -T -u blits bitcoind bitcoin-cli -regtest"
$BCLI createwallet miner
$BCLI -rpcwallet=miner -generate 111

# wait for electrs to have completed startup
until $COMPOSE logs electrs |grep 'finished full compaction'; do
    sleep 1
done

# wait for proxy to have completed startup
until $COMPOSE logs proxy |grep 'App is running at http://localhost:3000'; do
    sleep 1
done

