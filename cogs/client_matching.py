from .cogmixin import CogMixin
import discord

import socket
import binascii
import asyncio
from datetime import (datetime, timedelta)
import logging

logger = logging.getLogger(__name__)

def get_client_ch(bot):
    return discord.utils.get(bot.get_all_channels(), name="client")

def atob(address):
    return (address[0]).to_bytes(2, byteorder='big')+socket.inet_aton(address[1])


def btoa(_bytes):
    return (int.from_bytes(_bytes[0:2], 'big'), socket.inet_ntoa(_bytes[2:]))


class Th123Packet(object):
    def __init__(self, raw):
        self.raw = raw
        self.ip = None
        self.port = None
        self.matching_flag = None
        self.profile_length = None
        self.profile_name = None
        self.header = raw[0]
        if self.is_(1):
            self.port, self.ip = btoa(raw[3:9])
        elif self.is_(5):
            self.matching_flag = raw[25]
            self.profile_length = raw[26]
            self.profile_name = raw[27: 27+self.profile_length].decode("shift-jis")

    def is_(self, b):
        return self.header == b

    def __str__(self):
        return (f"port:{self.port}\n"
                f"ip:{self.ip}\n"
                f"flag:{self.matching_flag}\n"
                f"plength:{self.profile_length}\n"
                f"profile_name:{self.profile_name}\n"
                f"raw:{binascii.hexlify(self.raw)}\n"
                )


packet_03 = binascii.unhexlify("03")
packet_06 = binascii.unhexlify(
    "06000000" "00100000" "00440000" "00"
    "5368616e" "67686169" "00000000" "00000000" "00000000" "00000000" "00"
    "00000000" "000000"
    "00000000" "00000000" "00000000" "00000000" "00000000" "00000000" "00"
    "00000000" "00000000" "000000"
)

packet_07 = binascii.unhexlify(
    "0701" "000000"
)

# host to client
packet_0d_sp = binascii.unhexlify("0d0203")
packet_0d_parts = [binascii.unhexlify(p) for p in ["0d03", "030200000000"]]

def get_packet_0d(ack):
    return packet_0d_parts[0]+(ack).to_bytes(4, byteorder='little')+packet_0d_parts[1]


def get_ack(data):
    return int.from_bytes(data, 'little')


# host to watcher
def get_packet_08(port_ip):
    return binascii.unhexlify((
        "08010000" "000200"
        "%b" "00000000" "00000000"
        "70000000" "0000060c" "00000000" "00100000" "00000000" "00010e03"
        "97ce0010" "10101010" "10000000" "010d0905" "0e010000" "00100000"
    ).encode("ascii") % port_ip)


def get_packet_02(port_ip):
    return binascii.unhexlify(
        ("020200" "%b" "00000000" "00000000" "00000000").encode("ascii") % port_ip
    )


class Th123HolePunchingProtocol:
    def __init__(self, bot):
        self.bot = bot

    def connection_made(self, transport):
        self.transport = transport
        self.client_addr = None
        self.watcher_addr = None
        self.ack_datetime = datetime.now()
        self.ack_lifetime = timedelta(seconds=2)

    def datagram_received(self, data, addr):
        packet = Th123Packet(data)
        if datetime.now() - self.ack_datetime > self.ack_lifetime:
            self.client_addr = None
            self.watcher_addr = None
        self.ack_datetime = datetime.now()
        if packet.is_(1):
            if self.watcher_addr is None:
                self.transport.sendto(packet_03, addr)
            else:
                wip, wport = self.watcher_addr
                hexstr_wport_wip = binascii.hexlify(atob((wport, wip)))
                self.transport.sendto(get_packet_02(hexstr_wport_wip), self.client_addr)
        elif packet.is_(5):
            if self.client_addr is None:
                if not packet.matching_flag:
                    self.transport.sendto(packet_07, addr)
                else:
                    discord.compat.create_task(self.bot.send_message(
                        get_client_ch(self.bot), packet.profile_name
                    ))
                    self.transport.sendto(packet_06, addr)
                    self.ack = 1
            else:
                if packet.matching_flag:
                    self.transport.sendto(packet_07, addr)
                else:
                    cip, cport = self.client_addr
                    hexstr_cport_cip = binascii.hexlify(atob((cport, cip)))
                    self.transport.sendto(get_packet_08(hexstr_cport_cip), addr)
                    self.watcher_addr = addr
        elif packet.is_(14):
            if len(packet.raw) == 3:
                self.transport.sendto(packet_0d_sp, addr)
                self.client_addr = addr
            else:
                self.transport.sendto(get_packet_0d(self.ack), addr)
                if packet.raw[2:6] == b"\xff\xff\xff\xff":
                    self.ack = get_ack(packet.raw[2:6])
                else:
                    self.ack = get_ack(packet.raw[2:6])+1
        else:
            self.transport.sendto(data, addr)


class ClientMatching(CogMixin):
    def __init__(self, bot):
        self.bot = bot
        listen = self.bot.loop.create_datagram_endpoint(
            lambda: Th123HolePunchingProtocol(bot),
            local_addr=('0.0.0.0', 38100))
        discord.compat.create_task(listen)