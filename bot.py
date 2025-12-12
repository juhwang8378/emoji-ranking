import logging
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

import discord
import emoji
from discord import app_commands

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TIMEFRAME_LABELS = {
    "1주": timedelta(days=7),
    "1개월": timedelta(days=30),
    "3개월": timedelta(days=90),
    "전체": None,
}


class EmojiRankingClient(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.reactions = True
        intents.guilds = True
        intents.emojis = True
        intents.members = False
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        await self.tree.sync()

    async def on_ready(self) -> None:
        logger.info("봇이 로그인되었습니다: %s", self.user)


def parse_timeframe(label: str) -> timedelta | None:
    cleaned = label.strip()
    return TIMEFRAME_LABELS.get(cleaned)


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


async def resolve_emojis(bot: discord.Client, guild: discord.Guild, keys: list[str]) -> list[str]:
    resolved: list[str] = []
    for key in keys:
        partial = discord.PartialEmoji.from_str(key)
        if partial.is_custom_emoji():
            emoji_obj = guild.get_emoji(int(partial.id)) if partial.id else None
            resolved.append(str(emoji_obj) if emoji_obj else key)
        else:
            resolved.append(key)
    return resolved


client = EmojiRankingClient()


def _guild_or_error(interaction: discord.Interaction) -> discord.Guild:
    if not interaction.guild:
        raise RuntimeError("길드 컨텍스트에서만 사용할 수 있습니다.")
    return interaction.guild


@client.tree.command(name="emoji_rank", description="기간별 상위 20 이모지 사용량을 순위로 표시")
@app_commands.describe(기간="1주, 1개월, 3개월, 전체 중 하나 (미선택 시 전체)")
@app_commands.choices(
    기간=[
        app_commands.Choice(name="1주", value="1주"),
        app_commands.Choice(name="1개월", value="1개월"),
        app_commands.Choice(name="3개월", value="3개월"),
        app_commands.Choice(name="전체", value="전체"),
    ]
)
async def emoji_leaderboard(
    interaction: discord.Interaction, 기간: app_commands.Choice[str] | None = None
) -> None:
    label = 기간.value if 기간 else "전체"
    timeframe = parse_timeframe(label)
    since = None
    if timeframe:
        since = datetime.now(timezone.utc) - timeframe

    await interaction.response.defer(thinking=True)
    guild = _guild_or_error(interaction)
    counts = await collect_emoji_counts(guild, since)

    if not counts:
        await interaction.followup.send("아직 사용된 이모지가 없습니다.")
        return

    top_20 = counts.most_common(20)
    labels = await resolve_emojis(client, guild, [name for name, _ in top_20])
    counts_only = [count for _, count in top_20]
    lines = [f"{idx + 1}위: {emoji_label} [{count}]" for idx, (emoji_label, count) in enumerate(zip(labels, counts_only))]

    title = f"상위 20 이모지 사용량 ({label})"
    await interaction.followup.send(f"**{title}**\n" + "\n".join(lines))


@client.tree.command(name="emoji_unused", description="최근 30일 동안 5회 미만 사용된 커스텀 이모지 목록")
async def underused_emojis(interaction: discord.Interaction) -> None:
    guild = _guild_or_error(interaction)
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if not member or not member.guild_permissions.manage_emojis_and_stickers:
        await interaction.response.send_message("이 명령어는 관리자 전용입니다.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True, ephemeral=True)
    since = datetime.now(timezone.utc) - timedelta(days=30)
    counts = await collect_emoji_counts(guild, since)

    custom_emoji_keys = [str(emoji_obj) for emoji_obj in guild.emojis]
    underused = [key for key in custom_emoji_keys if counts.get(key, 0) < 5]
    if not underused:
        await interaction.followup.send("지난 30일 동안 5회 미만으로 사용된 이모지가 없습니다.", ephemeral=True)
        return

    labels = await resolve_emojis(client, guild, underused)
    await interaction.followup.send(
        "지난 30일 동안 5회 미만으로 사용된 이모지 목록:\n" + " ".join(labels),
        ephemeral=True,
    )


def main() -> None:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN 환경 변수를 설정해주세요.")
    client.run(token)


if __name__ == "__main__":
    main()
