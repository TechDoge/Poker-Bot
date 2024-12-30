import aiosqlite
from discord.ext import commands
from discord.ext.commands import Context
import discord
import matplotlib.pyplot as plt
import io
from discord import File
from datetime import datetime, timedelta
import requests
from PIL import Image
import numpy as np


class MoneyManager:
    def __init__(self, *, connection: aiosqlite.Connection) -> None:
        self.connection = connection

    async def initialize(self) -> None:
        """
        Initializes the database tables if they do not exist.
        """
        await self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance REAL NOT NULL
            )
            """
        )
        await self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS balance_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                change REAL NOT NULL,
                reason TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            """
        )
        await self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS games_played (
                user_id INTEGER NOT NULL,
                timestamp DATETIME NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            """
        )
        await self.connection.commit()

    async def initialize_user(self, user_id: int) -> None:
        """
        Ensures a user exists in the database with a default balance of 0.

        :param user_id: The ID of the user.
        """
        await self.connection.execute(
            """
            INSERT OR IGNORE INTO users (user_id, balance)
            VALUES (?, 0)
            """,
            (user_id,),
        )
        await self.connection.commit()

    async def update_balance(self, user_id: int, change: float, reason: str = None) -> None:
        """
        Updates a user's balance and logs the change.

        :param user_id: The ID of the user.
        :param change: The amount to add or subtract from the user's balance.
        :param reason: The reason for the balance change.
        """
        async with self.connection.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?", (change, user_id)
        ):
            await self.connection.commit()
        
        async with self.connection.execute(
            "INSERT INTO balance_changes (user_id, change, reason) VALUES (?, ?, ?)", (user_id, change, reason)
        ):
            await self.connection.commit()

    async def get_balance(self, user_id: int) -> float:
        """
        Retrieves a user's balance.

        :param user_id: The ID of the user.
        :return: The user's balance.
        """
        async with self.connection.execute(
            "SELECT balance FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            result = await cursor.fetchone()
            return result[0] if result else 0.0

    async def get_leaderboard(self, limit: int = 10) -> list:
        """
        Retrieves the top users by balance.

        :param limit: The number of top users to retrieve.
        :return: A list of tuples containing user_id and balance.
        """
        async with self.connection.execute(
            "SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT ?",
            (limit,),
        ) as cursor:
            return await cursor.fetchall()

    async def track_game(self, user_id: int) -> None:
        """
        Tracks a game played by the user.

        :param user_id: The ID of the user.
        """
        await self.connection.execute(
            "INSERT INTO games_played (user_id, timestamp) VALUES (?, ?)",
            (user_id, datetime.utcnow())
        )
        await self.connection.commit()

    async def get_games_played(self, user_id: int) -> int:
        """
        Retrieves the number of games played by a user.

        :param user_id: The ID of the user.
        :return: The number of games played.
        """
        async with self.connection.execute(
            "SELECT COUNT(DISTINCT strftime('%Y-%m-%d %H', timestamp)) FROM games_played WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            result = await cursor.fetchone()
            return result[0] if result else 0

    async def get_game_leaderboard(self, limit: int = 10) -> list:
        """
        Retrieves the top users by games played.

        :param limit: The number of top users to retrieve.
        :return: A list of tuples containing user_id and games played.
        """
        async with self.connection.execute(
            """
            SELECT user_id, COUNT(DISTINCT strftime('%Y-%m-%d %H', timestamp)) as games_played
            FROM games_played
            GROUP BY user_id
            ORDER BY games_played DESC
            LIMIT ?
            """,
            (limit,)
        ) as cursor:
            return await cursor.fetchall()


class MoneyCog(commands.Cog, name="money"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.db_manager = None

    async def cog_load(self) -> None:
        # Initialize the database connection and manager
        self.bot.db_connection = await aiosqlite.connect("database/pokerbot.db")
        self.db_manager = MoneyManager(connection=self.bot.db_connection)
        await self.db_manager.initialize()

    async def cog_unload(self) -> None:
        await self.bot.db_connection.close()

    @commands.hybrid_command(
        name="profit",
        description="Adjust your balance by a specific amount."
    )
    async def profit(self, context: Context, amount: float) -> None:
        """
        Adjusts the user's balance by a specified amount.

        :param context: The command context.
        :param amount: The amount to add or subtract from the user's balance.
        """
        user_id = context.author.id
        await self.db_manager.initialize_user(user_id)
        await self.db_manager.update_balance(user_id, amount)
        await self.db_manager.track_game(user_id)
        new_balance = await self.db_manager.get_balance(user_id)
        
        embed = discord.Embed(
            title="Balance Update",
            description=f"{context.author.mention}, your new balance is: **{'-$' if new_balance < 0 else '$'}{abs(new_balance):.2f}**",
            color=discord.Color.green()
        )
        
        await context.send(embed=embed)

    @commands.hybrid_command(
        name="balance",
        description="Check your current balance."
    )
    async def balance(self, context: Context) -> None:
        """
        Checks the user's current balance.

        :param context: The command context.
        """
        user_id = context.author.id
        await self.db_manager.initialize_user(user_id)
        balance = await self.db_manager.get_balance(user_id)
        
        embed = discord.Embed(
            title="Balance Check",
            description=f"{context.author.mention}, your current balance is: **{'-$' if balance < 0 else '$'}{abs(balance):.2f}**",
            color=discord.Color.blue()
        )
        
        await context.send(embed=embed)

    @commands.hybrid_command(
        name="leaderboard",
        description="Display the top users by balance."
    )
    async def leaderboard(self, context: Context, top_n: int = 20) -> None:
        """
        Displays the top users by balance.

        :param context: The command context.
        :param top_n: The number of top users to display.
        """
        top_users = await self.db_manager.get_leaderboard(top_n)
        if not top_users:
            await context.send("The leaderboard is empty!")
            return

        embed = discord.Embed(
            title="🏆 Leaderboard 🏆",
            description="Top users by balance",
            color=discord.Color.gold()
        )

        for rank, (user_id, balance) in enumerate(top_users, start=1):
            try:
                user = await self.bot.fetch_user(user_id)
                username = user.mention if user else f"User {user_id}"
            except Exception:
                username = f"User {user_id}"

            username.replace("@", "@$")
            embed.add_field(
                name=f"**#{rank}**",
                value=f"{username}: **{'-$' if balance < 0 else '$'}{abs(balance):.2f}**",
                inline=False
            )

        await context.send(embed=embed)

    @commands.hybrid_command(
        name="excess",
        description="Calculate the discrepancy between total winnings and losses."
    )
    async def excess(self, context: Context) -> None:
        """
        Calculates the discrepancy between total winnings and losses.

        :param context: The command context.
        """
        async with self.bot.db_connection.execute(
            "SELECT SUM(balance) FROM users"
        ) as cursor:
            result = await cursor.fetchone()
            total_balance = result[0] if result else 0.0

        detail = "Money needs to be removed among users." if total_balance > 0 else "Money needs to be added among users."
        if abs(total_balance) < 0.01:
            detail = "Users are perfectly balanced, as all things should be."

        embed = discord.Embed(
            title="Excess Calculation",
            description=f"The total discrepancy is: **{'-$' if total_balance < 0 else '$'}{abs(total_balance):.2f}**\n{detail}",
            color=discord.Color.red() if abs(total_balance) >= 0.01 else discord.Color.green()
        )
        
        await context.send(embed=embed)

    @commands.hybrid_command(
        name="split-excess",
        description="Split the excess balance among mentioned users or all users if no mentions."
    )
    @commands.has_permissions(administrator=True)
    async def split_excess(self, context: Context, mentions: commands.Greedy[discord.Member] = None) -> None:
        """
        Splits the excess balance among mentioned users or all users if no mentions.

        :param context: The command context.
        :param mentions: The users to split the excess with.
        """
        async with self.bot.db_connection.execute(
            "SELECT SUM(balance) FROM users"
        ) as cursor:
            result = await cursor.fetchone()
            total_balance = result[0] if result else 0.0

        if total_balance == 0:
            await context.send("There is no excess to split.")
            return

        if not mentions:
            async with self.bot.db_connection.execute(
                "SELECT user_id FROM users"
            ) as cursor:
                user_ids = [row[0] for row in await cursor.fetchall()]
        else:
            user_ids = [member.id for member in mentions]

        if not user_ids:
            await context.send("No users found to split the excess with.")
            return

        split_amount = -1 * (total_balance / len(user_ids))
        for user_id in user_ids:
            await self.db_manager.update_balance(user_id, split_amount)

        embed = discord.Embed(
            title="Split Excess",
            description=f"The excess of **{'-$' if total_balance < 0 else '$'}{abs(total_balance):.2f}** has been split among the {'mentioned users' if mentions else 'all users'}.",
            color=discord.Color.blue()
        )
        
        await context.send(embed=embed)

    @commands.hybrid_command(
        name="setbalance",
        description="Set the balance of mentioned users to a specific amount."
    )
    @commands.has_permissions(administrator=True)
    async def setbalance(self, context: Context, amount: float, mentions: commands.Greedy[discord.Member]) -> None:
        """
        Sets the balance of mentioned users to a specified amount.

        :param context: The command context.
        :param amount: The amount to set the balance to.
        :param mentions: The users to set the balance for.
        """
        if not mentions:
            await context.send("You must mention at least one user to set the balance for.")
            return

        for member in mentions:
            await self.db_manager.initialize_user(member.id)
            current_balance = await self.db_manager.get_balance(member.id)
            change = amount - current_balance
            await self.db_manager.update_balance(member.id, change)

        embed = discord.Embed(
            title="Set Balance",
            description=f"The balance of the mentioned users has been set to **{'-$' if amount < 0 else '$'}{abs(amount):.2f}**.",
            color=discord.Color.blue()
        )
        
        await context.send(embed=embed)

    @commands.hybrid_command(
        name="setbalanceid",
        description="Set the balance of users by their user ID to a specific amount."
    )
    @commands.has_permissions(administrator=True)
    async def set_balance_with_id(self, context: Context, amount: float, user_ids: commands.Greedy[int]) -> None:
        """
        Sets the balance of users by their user ID to a specified amount.

        :param context: The command context.
        :param amount: The amount to set the balance to.
        :param user_ids: The user IDs to set the balance for.
        """
        if not user_ids:
            await context.send("You must provide at least one user ID to set the balance for.")
            return

        for user_id in user_ids:
            await self.db_manager.initialize_user(user_id)
            current_balance = await self.db_manager.get_balance(user_id)
            change = amount - current_balance
            await self.db_manager.update_balance(user_id, change)

        embed = discord.Embed(
            title="Set Balance by ID",
            description=f"The balance of the specified users has been set to **{'-$' if amount < 0 else '$'}{abs(amount):.2f}**.",
            color=discord.Color.blue()
        )
        
        await context.send(embed=embed)

    @commands.hybrid_command(
        name="changebalance",
        description="Change the balance of users by their user ID or mention by a specific amount."
    )
    @commands.has_permissions(administrator=True)
    async def change_balance(self, context: Context, amount: float, targets: commands.Greedy[discord.Member] = None, user_ids: commands.Greedy[int] = None) -> None:
        """
        Changes the balance of users by their user ID or mention by a specified amount.

        :param context: The command context.
        :param amount: The amount to change the balance by.
        :param targets: The users to change the balance for.
        :param user_ids: The user IDs to change the balance for.
        """
        if not targets and not user_ids:
            await context.send("You must provide at least one user ID or mention to change the balance for.")
            return

        if targets:
            for member in targets:
                await self.db_manager.initialize_user(member.id)
                await self.db_manager.update_balance(member.id, amount)

        if user_ids:
            for user_id in user_ids:
                await self.db_manager.initialize_user(user_id)
                await self.db_manager.update_balance(user_id, amount)

        embed = discord.Embed(
            title="Change Balance",
            description=f"The balance of the specified users has been changed by **{'-$' if amount < 0 else '$'}{abs(amount):.2f}**.",
            color=discord.Color.blue()
        )
        
        await context.send(embed=embed)

    @commands.hybrid_command(
        name="history",
        description="Display the balance history of mentioned users."
    )
    async def balance_history(self, context: Context, mentions: commands.Greedy[discord.Member] = None) -> None:
        """
        Display the balance history of mentioned users or everyone if no mentions.

        :param context: The command context.
        :param mentions: The users to display the balance history for.
        """
        if not mentions:
            async with self.bot.db_connection.execute(
                "SELECT DISTINCT user_id FROM balance_changes"
            ) as cursor:
                user_ids = [row[0] for row in await cursor.fetchall()]
        else:
            user_ids = [member.id for member in mentions]

        if not user_ids:
            await context.send("No balance history available.")
            return

        file = await self.generate_balance_graph(user_ids)
        
        embed = discord.Embed(
            title="Balance History",
            description="Here is the balance history for the mentioned users." if mentions else "Here is the balance history for everyone.",
            color=0xBEBEFE
        )
        embed.set_image(url="attachment://balance_history.png")
        
        await context.send(embed=embed, file=file)

    async def generate_balance_graph(self, user_ids: list):
        fig, ax = plt.subplots()
        
        for user_id in user_ids:
            async with self.bot.db_connection.execute(
                "SELECT change, timestamp FROM balance_changes WHERE user_id = ? ORDER BY timestamp", (user_id,)
            ) as cursor:
                changes = await cursor.fetchall()
            
            if not changes:
                continue
            
            # Convert timestamps to PST
            timestamps = [datetime.strptime(change[1], '%Y-%m-%d %H:%M:%S') - timedelta(hours=8) for change in changes]
            balances = [sum(change[0] for change in changes[:i+1]) for i in range(len(changes))]
            
            # Add current balance as the last point
            current_balance = await self.db_manager.get_balance(user_id)
            now = datetime.utcnow() - timedelta(hours=8)
            timestamps.append(now)
            balances.append(current_balance)
            
            # Determine the time span
            time_span = (timestamps[-1] - timestamps[0]).days
            
            if time_span > 7:
                # More than a week of data, use days
                formatted_timestamps = [ts.strftime('%m/%d/%y') for ts in timestamps]
                step = max(1, len(formatted_timestamps) // 7)
            else:
                # Less than a week of data, use hours
                formatted_timestamps = [ts.strftime('%m/%d/%y-%I%p') for ts in timestamps]
                step = max(1, len(formatted_timestamps) // (7 * 24))
            
            # Reduce data points to fit the step
            formatted_timestamps = formatted_timestamps[::step]
            balances = balances[::step]
            
            user = await self.bot.fetch_user(user_id)

            user_avatar = user.avatar
            try:
                response = requests.get(user_avatar)
                # get mode color of user image and set it as line color (use numpy)
                img = Image.open(io.BytesIO(response.content))

                ax.plot(formatted_timestamps, balances, label=f"{user.display_name}", color=np.array(img).mean(axis=(0,1))/255)
            except Exception:
                ax.plot(formatted_timestamps, balances, label=f"{user.display_name}")
        ax.set_xlabel('Timestamp')
        ax.set_ylabel('Balance')
        ax.legend()
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        
        return File(buf, filename='balance_history.png')

    @commands.hybrid_command(
        name="games",
        description="Check how many games you've played."
    )
    async def games(self, context: Context) -> None:
        """
        Checks the number of games the user has played.

        :param context: The command context.
        """
        user_id = context.author.id
        await self.db_manager.initialize_user(user_id)
        games_played = await self.db_manager.get_games_played(user_id)
        
        embed = discord.Embed(
            title="Games Played",
            description=f"{context.author.mention}, you have played **{games_played}** games.",
            color=discord.Color.blue()
        )
        
        await context.send(embed=embed)

    @commands.hybrid_command(
        name="gameleaderboard",
        description="Display the top users by games played."
    )
    async def gameleaderboard(self, context: Context, top_n: int = 20) -> None:
        """
        Displays the top users by games played.

        :param context: The command context.
        :param top_n: The number of top users to display.
        """
        top_users = await self.db_manager.get_game_leaderboard(top_n)
        if not top_users:
            await context.send("The game leaderboard is empty.")
            return

        embed = discord.Embed(
            title="🏆 Game Leaderboard 🏆",
            description="Top users by games played",
            color=discord.Color.gold()
        )

        for rank, (user_id, games_played) in enumerate(top_users, start=1):
            try:
                user = await self.bot.fetch_user(user_id)
                username = user.mention if user else f"User {user_id}"
            except Exception:
                username = f"User {user_id}"

            username.replace("@", "@$")
            embed.add_field(
                name=f"**#{rank}**",
                value=f"{username}: **{games_played}** games",
                inline=False
            )

        await context.send(embed=embed)

async def setup(bot) -> None:
    await bot.add_cog(MoneyCog(bot))
