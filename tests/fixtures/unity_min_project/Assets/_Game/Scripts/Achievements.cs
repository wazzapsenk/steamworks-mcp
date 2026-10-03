using Steamworks;

public static class Achievements
{
    public static void FirstFort() { SteamUserStats.SetAchievement("ACH_FIRST_FORT"); SteamUserStats.StoreStats(); }

    public static void FortBuilt(int total)
    {
        SteamUserStats.SetStat("FORTS_BUILT", total);
        SteamUserStats.IndicateAchievementProgress("ACH_TEN_FORTS", (uint)total, 10);
        if (total >= 10) SteamUserStats.SetAchievement("ACH_TEN_FORTS");
    }

    public static void FindBoard()
    {
        SteamUserStats.FindOrCreateLeaderboard("FASTEST_FORT", ELeaderboardSortMethod.k_ELeaderboardSortMethodAscending, ELeaderboardDisplayType.k_ELeaderboardDisplayTypeTimeSeconds);
    }
}
