import aiosqlite
from discord.ext import commands
from discord.ext.commands import Context
import discord


class MoneyManager:
    def __init__(self, *, connection: aiosqlite.Connection) -> None:
        self.connection = connection

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

    async def update_balance(self, user_id: int, amount: float) -> None:
        """
        Updates a user's balance.

        :param user_id: The ID of the user.
        :param amount: The amount to add or subtract from the user's balance.
        """
        await self.connection.execute(
            """
            UPDATE users
            SET balance = balance + ?
            WHERE user_id = ?
            """,
            (amount, user_id),
        )
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


class MoneyCog(commands.Cog, name="money"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.db_manager = None

    async def cog_load(self) -> None:
        # Initialize the database connection and manager
        self.bot.db_connection = await aiosqlite.connect("database/pokerbot.db")
        self.db_manager = MoneyManager(connection=self.bot.db_connection)
        # Ensure the users table exists
        await self.bot.db_connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance REAL DEFAULT 0
            )
            """
        )
        await self.bot.db_connection.commit()

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
        if total_balance == 0:
            detail = "Users are perfectly balanced, as all things should be."

        embed = discord.Embed(
            title="Excess Calculation",
            description=f"The total discrepancy is: **{'-$' if total_balance < 0 else '$'}{abs(total_balance):.2f}**\n{detail}",
            color=discord.Color.red() if total_balance != 0 else discord.Color.green()
        )
        
        await context.send(embed=embed)

    @commands.hybrid_command(
        name="split-excess",
        description="Split the excess balance among mentioned users."
    )
    async def split_excess(self, context: Context, mentions: commands.Greedy[discord.Member]) -> None:
        """
        Splits the excess balance among mentioned users.

        :param context: The command context.
        :param mentions: The users to split the excess with.
        """
        if not mentions:
            await context.send("You must mention at least one user to split the excess with.")
            return

        async with self.bot.db_connection.execute(
            "SELECT SUM(balance) FROM users"
        ) as cursor:
            result = await cursor.fetchone()
            total_balance = result[0] if result else 0.0

        if total_balance == 0:
            await context.send("There is no excess to split.")
            return

        split_amount = -1*(total_balance / len(mentions))
        for member in mentions:
            await self.db_manager.update_balance(member.id, split_amount)

        embed = discord.Embed(
            title="Split Excess",
            description=f"The excess of **{'-$' if total_balance < 0 else '$'}{abs(total_balance):.2f}** has been split among the mentioned users.",
            color=discord.Color.blue()
        )
        
        await context.send(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(MoneyCog(bot))
