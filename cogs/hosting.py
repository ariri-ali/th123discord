from .cogmixin import CogMixin
from .common import errors

from discord.ext import commands
import discord

import unicodedata
import asyncio

import binascii
import socket
from datetime import (datetime, timedelta)
import logging

logger = logging.getLogger(__name__)

PACKET_TO_HOST = binascii.unhexlify(
    "056e7365" "d9ffc46e" "488d7ca1" "92313472"
    "95000000" "00280000" "00000000" "00000000"
    "00000000" "00000000" "00000000" "00000000"
    "00000000" "00000000" "00000000" "00000000" "00")
PACKET_TO_SOKUROLL = binascii.unhexlify(
    "05647365" "d9ffc46e" "488d7ca1" "92313472"
    "95000000" "00280000" "00000000" "00000000"
    "00000000" "00000000" "00000000" "00000000"
    "00000000" "00000000" "00000000" "00000000" "00")


def get_echo_packet(is_sokuroll=None):
    return PACKET_TO_SOKUROLL if is_sokuroll else PACKET_TO_HOST


def get_hostlist_ch(bot):
    return discord.utils.get(bot.get_all_channels(), name="hostlist")


class HostStatus:
    def __init__(self):
        self.hosting = False
        self.matching = False
        self.watchable = False

    def update_host_status(self, packet):
        if packet.startswith(b'\x07\x01'):
            self.hosting = True
            self.matching = False
            self.watchable = True
        elif packet.startswith(b'\x07\x00'):
            self.hosting = True
            self.matching = False
            self.watchable = False
        elif packet.startswith(b'\x08\x01'):
            self.hosting = True
            self.matching = True
            self.watchable = self.watchable
        else:
            self.hosting = False
            self.matching = False
            self.watchable = False

    def is_unknown(self):
        return (
            not self.hosting and
            not self.matching and
            not self.watchable)


class EchoClientProtocol:
    def __init__(self, user, host_message, echo_packet):
        self.user = user
        self.host_message = host_message
        self.echo_packet = echo_packet
        self.transport = None
        self.start_datetime = datetime.now()
        self.host_status = HostStatus()

        self.ack_datetime = datetime.now()

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.host_status.update_host_status(data)
        if self.host_status.is_unknown():
            logger.error(data)

        if not self.host_status.hosting:
            return

        self.ack_datetime = datetime.now()

    def error_received(self, exc):
        pass

    def connection_lost(self, exc):
        pass

    def try_echo(self):
        self.transport.sendto(self.echo_packet)

    def elapsed_time_from_ack(self):
        return datetime.now() - self.ack_datetime

    def get_host_message(self, ack_lost_threshold_time):
        if not self.host_status.hosting:
            return f":x: {self.host_message}"

        if self.elapsed_time_from_ack() >= ack_lost_threshold_time:
            return f":x: {self.host_message}"

        elapsed_seconds = (datetime.now() - self.start_datetime).seconds
        elapsed_time = f"{int(elapsed_seconds / 60)}m{elapsed_seconds % 60}s"
        return " ".join([
            ":crossed_swords:" if self.host_status.matching else ":o:",
            ":eye:" if self.host_status.watchable else ":see_no_evil:",
            elapsed_time,
            self.host_message])


class HostListObserver:
    WAIT = timedelta(seconds=2)
    LIFETIME = timedelta(seconds=WAIT.seconds * 10)

    _bot = None
    _host_list = []

    @classmethod
    async def update_hostlist(cls, bot):
        cls._bot = bot

        base_message = "**{}人が対戦相手を募集しています:**\n"
        message = await cls._bot.send_message(
            get_hostlist_ch(cls._bot),
            base_message.format(0))

        while True:
            host_list = cls._host_list[:]
            for host in host_list:
                host.try_echo()

            await asyncio.sleep(cls.WAIT.seconds)

            host_messages = list()
            for host in host_list:
                elapsed_time = host.elapsed_time_from_ack()
                if elapsed_time >= cls.LIFETIME:
                    close_message = (
                        "一定時間ホストが検知されなかったため、"
                        "募集を終了します。")
                    await cls.close(host, close_message)
                    continue

                host_messages.append(host.get_host_message(cls.WAIT * 3))

            post_message = (
                base_message.format(len(host_messages)) +
                "\n".join(host_messages))
            await cls._bot.edit_message(message, post_message)

    @classmethod
    async def close(cls, host, close_message):
        await cls._bot.send_message(host.user, close_message)
        cls._remove(host)
        host.transport.close()

    @classmethod
    def append(cls, host):
        cls._host_list.append(host)

    @classmethod
    def _remove(cls, host):
        cls._host_list.remove(host)


class Hosting(CogMixin):
    def __init__(self, bot):
        self.bot = bot
        self.observer = None

    @commands.command(pass_context=True)
    async def host(self, ctx, ip_port: str, *comment):
        """
        #holtlistに対戦募集を投稿します。
        約20秒間ホストが検知されなければ、自動で投稿を取り下げます。
        募集例「!host 123.456.xxx.xxx:10800 霊夢　レート1500　どなたでもどうぞ！」
        """
        if self.observer is None:
            self.observer = discord.compat.create_task(
                HostListObserver.update_hostlist(self.bot))

        user = ctx.message.author
        ip, port = unicodedata.normalize('NFKC', ip_port).split(":")
        try:
            int(port)
        except ValueError:
            raise commands.BadArgument
        ip_port_comments = f"{ip}:{port} | {' '.join(comment)}"
        host_message = f"{user.mention}, {ip_port_comments}"

        not_private = not ctx.message.channel.is_private
        if not_private:
            await self.bot.delete_message(ctx.message)
            raise errors.OnlyPrivateMessage

        await self.bot.whisper("ホストの検知を開始します。")
        connect = self.bot.loop.create_datagram_endpoint(
            lambda: EchoClientProtocol(
                user,
                host_message,
                get_echo_packet(is_sokuroll=False)),
            remote_addr=(ip, int(port)))
        _, protocol = await connect
        HostListObserver.append(protocol)

    @commands.command(pass_context=True)
    async def rhost(self, ctx, ip_port: str, *comment):
        """
        #holtlistにsokuroll有りの対戦募集を投稿します。
        約20秒間ホストが検知されなければ、自動で投稿を取り下げます。
        募集例「!host 123.456.xxx.xxx:10800 霊夢　レート1500　どなたでもどうぞ！」
        """
        if self.observer is None:
            self.observer = discord.compat.create_task(
                HostListObserver.update_hostlist(self.bot))

        user = ctx.message.author
        ip, port = unicodedata.normalize('NFKC', ip_port).split(":")
        try:
            int(port)
        except ValueError:
            raise commands.BadArgument
        ip_port_comments = f"{ip}:{port} | {' '.join(comment)}"
        host_message = f"{user.mention}, {ip_port_comments}"

        not_private = not ctx.message.channel.is_private
        if not_private:
            await self.bot.delete_message(ctx.message)
            raise errors.OnlyPrivateMessage

        await self.bot.whisper("ホストの検知を開始します。")
        connect = self.bot.loop.create_datagram_endpoint(
            lambda: EchoClientProtocol(
                user,
                ":regional_indicator_r:" + host_message,
                get_echo_packet(is_sokuroll=True)),
            remote_addr=(ip, int(port)))
        _, protocol = await connect
        HostListObserver.append(protocol)
