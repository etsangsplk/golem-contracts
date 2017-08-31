import pytest
import ethereum
import ethereum.utils as utils
from ethereum.tester import TransactionFailed
from secp256k1 import PrivateKey
from test_brass_oracle import mysetup, seconds
import eth_utils
from eth_utils import (
    event_signature_to_log_topic,
)


def test_close(chain):
    owner_addr, receiver_addr, gnt, gntw, cdep = mysetup(chain)
    pc = deploy_channels(chain, owner_addr, gntw)
    channel = prep_a_channel(chain, owner_addr, receiver_addr, gntw, pc)
    # bad caller
    with pytest.raises(TransactionFailed):
        chain.wait.for_receipt(
            pc.transact({'from': receiver_addr}).close(channel))
    assert pc.call().isLocked(channel)
    # unlock needed first
    with pytest.raises(TransactionFailed):
        chain.wait.for_receipt(
            pc.transact({'from': owner_addr}).close(channel))
    topics = [owner_addr, receiver_addr]
    f_id = log_filter(chain, pc.address,
                      "Unlock(address, address, bytes32)", topics)
    chain.wait.for_receipt(
        pc.transact({'from': owner_addr}).unlock(channel))
    logs = get_logs(f_id)
    assert len(logs) == 1
    achannel = logs[0]["data"]
    assert achannel == eth_utils.encode_hex(channel)
    # bad caller
    with pytest.raises(TransactionFailed):
        chain.wait.for_receipt(
            pc.transact({'from': receiver_addr}).close(channel))
    f_id = log_filter(chain, pc.address,
                      "Close(address, address, bytes32)", topics)
    # too early, still in close_delay period
    assert pc.call().isTimeLocked(channel)
    with pytest.raises(TransactionFailed):
        chain.wait.for_receipt(
            pc.transact({'from': owner_addr}).close(channel))
    while pc.call().isTimeLocked(channel):
        chain.web3.testing.mine(1)
    # proper close
    chain.wait.for_receipt(
        pc.transact({'from': owner_addr}).close(channel))
    logs = get_logs(f_id)
    assert len(logs) == 1
    achannel = logs[0]["data"]
    assert achannel == eth_utils.encode_hex(channel)


def test_withdraw(chain):
    owner_addr, receiver_addr, gnt, gntw, cdep = mysetup(chain)
    pc = deploy_channels(chain, owner_addr, gntw)
    channel = prep_a_channel(chain, owner_addr, receiver_addr, gntw, pc)

    def capacity():
        return pc.call().getDeposited(channel) - pc.call().getWithdrawn(channel)

    def shared_capacity():
        return gntw.call().balanceOf(pc.address)

    c_cap = capacity()
    sh_cap = shared_capacity()

    owner_priv = ethereum.tester.keys[9]
    V, ER, ES = sign_transfer(channel, owner_priv, receiver_addr, 10)
    with pytest.raises(TransactionFailed):
        chain.wait.for_receipt(
            pc.transact({"from": receiver_addr}).withdraw(channel, 1000,
                                                        V, ER, ES))
    with pytest.raises(TransactionFailed):
        chain.wait.for_receipt(
            pc.transact({"from": receiver_addr}).withdraw(channel, 5,
                                                        V, ER, ES))
    chain.wait.for_receipt(
        pc.transact({"from": receiver_addr}).withdraw(channel, 10,
                                                    V, ER, ES))
    assert capacity() == c_cap - 10
    assert shared_capacity() == sh_cap - 10

    with pytest.raises(TransactionFailed):
        chain.wait.for_receipt(
            pc.transact({"from": receiver_addr}).withdraw(channel, 10,
                                                        V, ER, ES))
    V, ER, ES = sign_transfer(channel, owner_priv, receiver_addr, 5)
    with pytest.raises(TransactionFailed):
        chain.wait.for_receipt(
            pc.transact({"from": receiver_addr}).withdraw(channel, 5,
                                                        V, ER, ES))
    V, ER, ES = sign_transfer(channel, owner_priv, receiver_addr, 20)
    chain.wait.for_receipt(
        pc.transact({"from": receiver_addr}).withdraw(channel, 20,
                                                    V, ER, ES))
    assert capacity() == c_cap - 20
    assert shared_capacity() == sh_cap - 20


def test_forceClose(chain):
    owner_addr, receiver_addr, gnt, gntw, cdep = mysetup(chain)
    pc = deploy_channels(chain, owner_addr, gntw)
    channel = prep_a_channel(chain, owner_addr, receiver_addr, gntw, pc)
    # bad caller
    with pytest.raises(TransactionFailed):
        chain.wait.for_receipt(
            pc.transact({'from': owner_addr}).forceClose(channel))
    topics = [owner_addr, receiver_addr]
    f_id = log_filter(chain, pc.address,
                      "ForceClose(address, address, bytes32)", topics)
    # proper caller
    chain.wait.for_receipt(
        pc.transact({'from': receiver_addr}).forceClose(channel))
    logs = get_logs(f_id)
    assert len(logs) == 1
    achannel = logs[0]["data"]
    assert achannel == eth_utils.encode_hex(channel)
    # duplicate call
    with pytest.raises(TransactionFailed):
        chain.wait.for_receipt(
            pc.transact({'from': receiver_addr}).forceClose(channel))
    # can't fund closed channel
    with pytest.raises(TransactionFailed):
        chain.wait.for_receipt(
            pc.transact({'from': owner_addr}).fund(channel, receiver_addr, 100))


def sign_transfer(channel, owner_priv, receiver_addr, amount):
    # in Solidity: sha3(channel, bytes32(_value)):
    msghash = utils.sha3(channel + cpack(32, amount))
    assert len(msghash) == 32
    (V, R, S) = sign_eth(msghash, owner_priv)
    ER = cpack(32, R)
    ES = cpack(32, S)
    return V, ER, ES


# HELPERS
def a2t(address):
    return eth_utils.encode_hex(utils.zpad(address, 32))


def prep_a_channel(chain, owner_addr, receiver_addr, gntw, pc):
    topics = [owner_addr, receiver_addr]
    f_id = log_filter(chain, pc.address,
                      "NewChannel(address, address, bytes32)", topics)
    tx = chain.wait.for_receipt(
        pc.transact({'from': owner_addr}).createChannel(receiver_addr))
    print("tx: {}".format(tx))
    logs = get_logs(f_id)
    channel = logs[0]["data"]
    print("channel: {}".format(channel))
    channel = eth_utils.decode_hex(channel[2:])

    thevalue = pc.call().getDeposited(channel)
    assert thevalue == 0

    thereceiver = pc.call().getReceiver(channel)
    assert thereceiver == eth_utils.encode_hex(receiver_addr)

    theowner = pc.call().getOwner(channel)
    assert theowner == eth_utils.encode_hex(owner_addr)

    assert 0 == pc.call().getDeposited(channel)
    deposit_size = 1234567
    chain.wait.for_receipt(
        gntw.transact({'from': owner_addr}).approve(pc.address,
                                                    deposit_size*2))
    topics = [receiver_addr, channel]
    f_id = log_filter(chain, pc.address,
                      "Fund(address, bytes32, uint256)", topics)
    tx = chain.wait.for_receipt(
        pc.transact({'from': owner_addr}).fund(channel, receiver_addr,
                                               deposit_size))
    print("tx: {}".format(tx))
    logs = get_logs(f_id)
    log_amount = int.from_bytes(eth_utils.decode_hex(logs[0]["data"]),
                                byteorder='big')
    assert log_amount == deposit_size
    assert deposit_size == pc.call().getDeposited(channel)
    assert eth_utils.encode_hex(owner_addr) == pc.call().getOwner(channel)
    return channel


def log_filter(chain, address, signature, topics):
    bn = chain.web3.eth.blockNumber
    topics = topics[:]
    if topics is not None:
        for i in range(len(topics)):
            topics[i] = a2t(topics[i])
    # "LogAnonymous()"
    # "NewChannel(address, address, bytes32)"
    enc_sig = eth_utils.encode_hex(event_signature_to_log_topic(signature))
    # enc_sig2 = rlp.utils.encode_hex(event_signature_to_log_topic(signature))
    # assert enc_sig == enc_sig2
    topics.insert(0, enc_sig)
    print("{} -> {}".format(signature, topics))
    obj = {
        'fromBlock': bn,
        'toBlock': "latest",
        'address': address,
        'topics': topics
    }
    return (chain, chain.web3.eth.filter(obj).filter_id, enc_sig)


def get_logs(f_id):
    chain, id, enc_sig = f_id
    logs = chain.web3.eth.getFilterLogs(id)
    print("logs: {}".format(logs))

    def l(x):
        print("topic: {}; enc_sig: {}".format(x["topics"][0], enc_sig))
        return (x["topics"][0]) == enc_sig

    lf = list(filter(l, logs))
    print("lf: {}".format(lf))
    return lf


def deploy_channels(chain, factory_addr, gntw):
    args = [gntw.address, seconds(600)]
    pc, tx = chain.provider.get_or_deploy_contract('GNTPaymentChannels',
                                                   deploy_transaction={
                                                       'from': factory_addr
                                                   },
                                                   deploy_args=args)
    chain.wait.for_receipt(tx)
    return pc


def cpack(n, bts):
    """Packs int into bytesXX"""
    import struct
    fmt = "!{}B".format(n)
    return struct.pack(fmt, *tobyteslist(n, bts))


def tobyteslist(n, bts):
    return [bts >> i & 0xff for i in reversed(range(0, n*8, 8))]


def sign_eth(rawhash, priv):
    pk = PrivateKey(priv, raw=True)
    signature = pk.ecdsa_recoverable_serialize(
        pk.ecdsa_sign_recoverable(rawhash, raw=True)
    )
    signature = signature[0] + utils.bytearray_to_bytestr([signature[1]])
    v = utils.safe_ord(signature[64]) + 27
    r = utils.big_endian_to_int(signature[0:32])
    s = utils.big_endian_to_int(signature[32:64])
    return (v, r, s)