import logging
import math
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

import discord
import emoji
from discord.ext import commands

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TIMEFRAME_LABELS = {
    "1주": timedelta(days=7),
    "1개월": timedelta(days=30),
    "3개월": timedelta(days=90),
    "전체": None,
}


class EmojiRankingBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.reactions = True
        intents.guilds = True
        intents.emojis = True
        intents.members = False
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def on_ready(self) -> None:
        logger.info("봇이 로그인되었습니다: %s", self.user)


def parse_timeframe(label: str) -> timedelta | None:
    cleaned = label.strip()
    return TIMEFRAME_LABELS.get(cleaned)


def format_vertical_graph(pairs: list[tuple[str, int]], height: int = 10) -> str:
    if not pairs:
        return "(데이터가 없습니다)"

    max_count = max(count for _, count in pairs)
    if max_count == 0:
        return "(데이터가 없습니다)"

    scaled_heights = [max(1, math.ceil(count / max_count * height)) for _, count in pairs]
    lines: list[str] = []
    for level in range(height, 0, -1):
        row = []
        for column_height in scaled_heights:
            row.append("  █  " if column_height >= level else "     ")
        lines.append("".join(row))

    labels = "".join(f" {name}  " for name, _ in pairs)
    counts = "".join(f" {count:^3} " for _, count in pairs)
    lines.append(labels)
    lines.append(counts)
    return "\n".join(lines)


def extract_emoji_counts_from_text(content: str) -> Counter[str]:
    counts: Counter[str] = Counter()

    for match in emoji.emoji_list(content):
        counts[match["emoji"]] += 1

    custom_pattern = re.compile(r"<a?:[\w~]+:(\d+)>")
    for match in custom_pattern.finditer(content):
        partial = discord.PartialEmoji.from_str(match.group(0))
        counts[str(partial)] += 1

    return counts


def merge_counts(target: Counter[str], source: Counter[str]) -> None:
    for key, value in source.items():
        target[key] += value


async def collect_emoji_counts(guild: discord.Guild, since: datetime | None) -> Counter[str]:
    counts: Counter[str] = Counter()
    for channel in guild.text_channels:
        if not channel.permissions_for(guild.me).read_messages:
            continue
        try:
            async for message in channel.history(limit=None, after=since, oldest_first=True):
                merge_counts(counts, extract_emoji_counts_from_text(message.content))

                for reaction in message.reactions:
                    emoji_key = str(reaction.emoji)
                    counts[emoji_key] += reaction.count
        except discord.Forbidden:
            logger.warning("채널 %s에 접근할 수 없습니다", channel.name)
        except discord.HTTPException as exc:
            logger.warning("채널 %s 조회 중 오류 발생: %s", channel.name, exc)
    return counts


async def resolve_emojis(bot: commands.Bot, guild: discord.Guild, keys: list[str]) -> list[str]:
    resolved: list[str] = []
    for key in keys:
        partial = discord.PartialEmoji.from_str(key)
        if partial.is_custom_emoji():
            emoji_obj = guild.get_emoji(int(partial.id)) if partial.id else None
            resolved.append(str(emoji_obj) if emoji_obj else key)
        else:
            resolved.append(key)
    return resolved


bot = EmojiRankingBot()


@bot.command(name="이모지랭킹")
async def emoji_leaderboard(ctx: commands.Context, 기간: str = "전체") -> None:
    timeframe = parse_timeframe(기간)
    if 기간 not in TIMEFRAME_LABELS:
        await ctx.send("사용 가능한 기간 옵션: 1주, 1개월, 3개월, 전체")
        return

    since = None
    if timeframe:
        since = datetime.now(timezone.utc) - timeframe

    await ctx.send("이모지 데이터를 모으는 중입니다... 잠시만 기다려 주세요.")
    counts = await collect_emoji_counts(ctx.guild, since)  # type: ignore[arg-type]

    if not counts:
        await ctx.send("아직 사용된 이모지가 없습니다.")
        return

    top_20 = counts.most_common(20)
    labels = await resolve_emojis(bot, ctx.guild, [name for name, _ in top_20])  # type: ignore[arg-type]
    labelled_pairs = list(zip(labels, [count for _, count in top_20]))
    graph = format_vertical_graph(labelled_pairs)

    title = f"상위 20 이모지 사용량 ({기간})"
    await ctx.send(f"**{title}**\n```\n{graph}\n```")


@bot.command(name="미사용이모지")
@commands.has_permissions(manage_emojis=True)
async def underused_emojis(ctx: commands.Context) -> None:
    since = datetime.now(timezone.utc) - timedelta(days=30)
    counts = await collect_emoji_counts(ctx.guild, since)  # type: ignore[arg-type]

    custom_emoji_keys = [str(emoji_obj) for emoji_obj in ctx.guild.emojis]  # type: ignore[arg-type]
    underused = [key for key in custom_emoji_keys if counts.get(key, 0) < 5]
    if not underused:
        await ctx.send("지난 30일 동안 5회 미만으로 사용된 이모지가 없습니다.")
        return

    labels = await resolve_emojis(bot, ctx.guild, underused)  # type: ignore[arg-type]
    await ctx.send("지난 30일 동안 5회 미만으로 사용된 이모지 목록:\n" + " ".join(labels))


@underused_emojis.error
async def underused_emojis_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("이 명령어는 관리자 전용입니다.")
    else:
        await ctx.send("명령어 실행 중 문제가 발생했습니다. 잠시 후 다시 시도해주세요.")
        logger.error("underused_emojis 오류", exc_info=error)


def main() -> None:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN 환경 변수를 설정해주세요.")
    bot.run(token)


if __name__ == "__main__":
    main()
