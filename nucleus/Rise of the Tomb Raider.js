// Nucleus Co-op handler: Rise of the Tomb Raider - Endurance co-op on one PC (2 instances, 1 controller each).
// Adapted from Talos91's "Tomb Raider" (2013) handler (hub.splitscreen.me/handler/H3Z5aSGwrwzsDzNuJ).
//
// ROTTR facts this handler relies on (read from the installed game, Steam build 9575745):
//   - single-instance lock: mutex "TR2MUTEX" (UTF-16 string in ROTTR.exe)
//   - Steam API: steam_api64.dll (SteamUser019 / SteamMatchMaking009 / SteamNetworking005)
//   - Endurance co-op lobbies + login: Epic Online Services SDK 1.14.2 (EOSSDK-Win64-Shipping.dll), P2P ports 7777-7876
//     -> the in-game invite code is an EOS lobby id
//   - settings: HKCU\Software\Crystal Dynamics\Rise of the Tomb Raider\Graphics (same layout as TR2013)
//   - controllers: XInput via xinput1_3.dll / xinput9_1_0.dll
//
// v3 (2026-10-19), from test results:
//   - v1: Goldberg + Epic emu + Epic launch args (-AUTH_TYPE=exchangecode -EpicPortal ...) -> instance 1 crashed in the
//     front-end UI (null read in ROTTR.exe, Scaleform call chain). v2 run: instance 1 with Goldberg + Epic emu DLL but
//     NO Epic args reached the menus fine -> the args were the problem, not the emulator.
//   - Nucleus runs Game.Play() only AFTER player 1's emulator setup, so emulator choices made in Play() apply from
//     player 2 on. Emulator settings are therefore fixed here, not options (v2's option left player 2 without any
//     EOSSDK dll, so instance 2 could not start).
//   - Each instance still gets its own Epic identity (nepice_settings\NemirtingasEpicEmu.json: player1/player2, LAN on).

var yesno = ["No", "Yes"];
Game.AddOption("Use DirectX 12", "DX11 is usually safer when running two instances.", "DX12", yesno);
Game.AddOption("Player 2 small window (800x600)", "Player 2 renders in an 800x600 window (AI partner doesn't need a big view). Use a small Manual layout box for Player 2 in Nucleus.", "P2Small", ["Yes", "No"]);
Game.AddOption("Player 2 low graphics", "Lowest quality settings for Player 2 only (bot/AI partner doesn't need visuals; saves RAM/GPU).", "P2Low", ["Yes", "No"]);
Game.AddOption("Player 1 = my real Steam account", "Player 1 uses real Steam (achievements count); co-op still runs over the emulated Epic LAN. Steam must be running.", "RealSteam", ["Yes", "No"]);

Game.ExecutableName = "ROTTR.exe";
Game.ExecutableContext = ["bigfile.000.tiger"];
Game.SteamID = "391220";
Game.GUID = "Rise of the Tomb Raider";
Game.GameName = "Rise of the Tomb Raider";
Game.MaxPlayers = 2;               // Endurance co-op is 2 players
Game.MaxPlayersOneMonitor = 2;

// second instance: the game refuses to start while TR2MUTEX exists
Game.KillMutex = ["TR2MUTEX"];
Game.KillMutexType = "Mutant";

// each instance gets its own symlinked copy; these files must be real per-instance copies
Game.SymlinkExe = false;
Game.SymlinkGame = true;
Game.SymlinkFolders = true;
Game.FileSymlinkExclusions = ["steam_api64.dll", "steam_interfaces.txt", "steam_appid.txt",
                              "EOSSDK-Win64-Shipping.dll", "xinput1_3.dll", "xinput9_1_0.dll"];

// Off: Documents is redirected to OneDrive on this PC, which breaks Nucleus' per-instance user folders.
// ROTTR only writes a log to Documents; per-instance Steam profiles live in the instance folder (Goldberg local_save).
Game.UseNucleusEnvironment = false;

// online emulation for BOTH players (no second Steam account needed)
Game.NeedsSteamEmulation = false;
Game.UseGoldberg = true;
Game.GoldbergExperimental = true;
Game.GoldbergNoLocalSave = false;  // keep each instance's profile separate
Game.UserProfileSavePath = "AppData\\Roaming\\Goldberg SteamEmu Saves\\391220\\remote";
Game.UseNemirtingasEpicEmu = true; // places the emulator's EOSSDK-Win64-Shipping.dll + per-player identity json
Game.EpicEmuArgs = false;          // NO Epic store launch args: they crashed the front-end in v1

// controllers: one per instance, no keyboard
Game.SupportsKeyboard = false;
Game.Hook.DInputEnabled = false;
Game.Hook.DInputForceDisable = true;
Game.Hook.XInputEnabled = true;
Game.Hook.XInputReroute = false;
Game.XInputPlusDll = ["xinput1_3.dll", "xinput9_1_0.dll"];
Game.Hook.CustomDllEnabled = false;

// both windows must keep running when not focused
Game.Hook.ForceFocus = false;
Game.Hook.ForceFocusWindowName = "Rise of the Tomb Raider";
Game.FakeFocus = true;
Game.HookFocus = true;
Game.HideTaskbar = false;
Game.HandlerInterval = 100;
Game.PauseBetweenStarts = 40;      // ROTTR loads slowly; let instance 1 finish starting
Game.StartArguments = "";

Game.Play = function() {
  var key = "SOFTWARE\\Crystal Dynamics\\Rise of the Tomb Raider\\Graphics";
  // borderless window sized to this player's screen area (registry is shared, so it is rewritten before each launch)
  Context.EditRegKey("HKEY_CURRENT_USER", key, "ExclusiveFullscreen", 0, Nucleus.RegType.DWord);
  Context.EditRegKey("HKEY_CURRENT_USER", key, "Fullscreen", 1, Nucleus.RegType.DWord);
  Context.EditRegKey("HKEY_CURRENT_USER", key, "FullscreenWidth", Context.Width, Nucleus.RegType.DWord);
  Context.EditRegKey("HKEY_CURRENT_USER", key, "FullscreenHeight", Context.Height, Nucleus.RegType.DWord);
  Context.EditRegKey("HKEY_CURRENT_USER", key, "AspectRatio", 0, Nucleus.RegType.DWord);
  Context.EditRegKey("HKEY_CURRENT_USER", key, "EnableDX12", Context.Options["DX12"] == "Yes" ? 1 : 0, Nucleus.RegType.DWord);

  // v7: Player 2 as a small 800x600 window instead of borderless fullscreen (render size = window size)
  if (Context.PlayerID == 1 && Context.Options["P2Small"] == "Yes") {
    Context.EditRegKey("HKEY_CURRENT_USER", key, "Fullscreen", 0, Nucleus.RegType.DWord);
    Context.EditRegKey("HKEY_CURRENT_USER", key, "WindowWidth", 800, Nucleus.RegType.DWord);
    Context.EditRegKey("HKEY_CURRENT_USER", key, "WindowHeight", 600, Nucleus.RegType.DWord);
    Context.EditRegKey("HKEY_CURRENT_USER", key, "UserWindowWidth", 800, Nucleus.RegType.DWord);
    Context.EditRegKey("HKEY_CURRENT_USER", key, "UserWindowHeight", 600, Nucleus.RegType.DWord);
    Context.EditRegKey("HKEY_CURRENT_USER", key, "WindowMaximized", 0, Nucleus.RegType.DWord);
  }

  // v6: Player 2 at lowest quality (shared registry is written right before each launch; Nucleus backs up and
  // restores the Graphics key, see utils\backup\Graphics.reg). Player 1 keeps your own settings.
  var low = ["TextureQuality", "TextureFiltering", "ShadowQuality", "SoftShadowQuality", "SSAAQuality", "DOFQuality",
             "MotionBlur", "AmbientOcclusionQuality", "Bloom", "VignetteBlur", "LevelOfDetail", "DynamicFoliage",
             "Tessellation", "LensFlares", "ScreenEffects", "FilmGrain", "ScreenSpaceReflections", "TressFX",
             "SpecularReflectionQuality", "SMAA", "FXAA"];
  for (var q = 0; q < low.length; q++) {
    if (Context.PlayerID == 1 && Context.Options["P2Low"] == "Yes") {
      Context.EditRegKey("HKEY_CURRENT_USER", key, low[q], 0, Nucleus.RegType.DWord);
    }
  }

  // Goldberg: Steam interface versions used by ROTTR.exe (read by the game at start, so writing it here is in time)
  Context.WriteTextFile(Context.GetFolder(Nucleus.Folder.InstancedGameFolder) + "\\steam_interfaces.txt", [
    // v8: ALL interfaces ROTTR.exe asks for (was 3 -> P2 quit cleanly at the pause menu "Friends' Scores" and at the
    // end-of-run score screen = SteamUserStats leaderboards; P1 on real Steam was fine)
    "SteamClient017", "SteamUser019", "SteamFriends015", "SteamUtils009", "SteamMatchMaking009",
    "SteamMatchMakingServers002", "STEAMUSERSTATS_INTERFACE_VERSION011", "STEAMAPPS_INTERFACE_VERSION008",
    "SteamNetworking005", "STEAMREMOTESTORAGE_INTERFACE_VERSION014", "STEAMSCREENSHOTS_INTERFACE_VERSION003",
    "STEAMHTTP_INTERFACE_VERSION002", "SteamController006", "STEAMUGC_INTERFACE_VERSION010",
    "STEAMAPPLIST_INTERFACE_VERSION001", "STEAMMUSIC_INTERFACE_VERSION001", "STEAMMUSICREMOTE_INTERFACE_VERSION001",
    "STEAMHTMLSURFACE_INTERFACE_VERSION_004", "STEAMINVENTORY_INTERFACE_V002", "STEAMVIDEO_INTERFACE_V002",
    "STEAMPARENTALSETTINGS_INTERFACE_VERSION001"
  ]);
  Context.WriteTextFile(Context.GetFolder(Nucleus.Folder.InstancedGameFolder) + "\\disable_lan_only.txt", [""]);

  // Audio fix (v4): ROTTR starts FMOD Ex with FMOD_INIT_ENABLE_PROFILE (0x20), which opens the FMOD profiler's network
  // port. The 2nd instance can't open the same port -> "FMOD Failed Initialization (A socket error occurred)" -> fatal.
  // System::init is called 3x with "mov r8d, 0x802000A0" (41 B8 A0 00 20 80; exactly 3 matches in build 9575745,
  // at RVA 0x3b6cdf / 0x3b6d0f / 0x3b6d6e). Patch the instance copy only: flags 0x80200080 = same flags, profiler off.
  Context.PatchFileFindAll(System.IO.Path.Combine(Context.RootInstallFolder, "ROTTR.exe"),
    System.IO.Path.Combine(Context.RootFolder, "ROTTR.exe"),
    [0x41, 0xB8, 0xA0, 0x00, 0x20, 0x80],
    [0x41, 0xB8, 0x80, 0x00, 0x20, 0x80]);

  // v5: player 1 on the REAL Steam account (achievements, own profile). Co-op itself goes through EOS, which stays
  // emulated (Nemirtinga's EOSSDK dll) for both players, so they still find each other on the local network.
  // Play() runs after Nucleus' Goldberg setup and before launch, so undo Goldberg for player 1 only.
  if (Context.PlayerID == 0 && Context.Options["RealSteam"] == "Yes") {
    var inst = Context.RootFolder;
    System.IO.File.Copy(System.IO.Path.Combine(Context.RootInstallFolder, "steam_api64.dll"),
                        System.IO.Path.Combine(inst, "steam_api64.dll"), true);
    var gb = ["steamclient.dll", "steamclient64.dll"];
    for (var i = 0; i < gb.length; i++) {
      var p = System.IO.Path.Combine(inst, gb[i]);
      if (System.IO.File.Exists(p)) { System.IO.File.Delete(p); }
    }
    // steam_appid.txt (391220) stays: lets the real steam_api run from the instance folder with Steam open
  }
};
