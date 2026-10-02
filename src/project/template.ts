export function manifestTemplate(name: string, appId?: number): string {
  return `# steamworks.yaml — single source of truth for this game's Steam store page and Steamworks settings.
# Text here is written in \`sourceLanguage\`. Translations live in localization/<language>.yaml.
# Language codes are Steam API codes: english, turkish, german, french, schinese, tchinese, japanese, koreana, brazilian, latam, ...

${appId ? `appId: ${appId}` : "# appId: 123456"}
name: ${JSON.stringify(name)}
sourceLanguage: english
targetLanguages: [turkish, german, french, spanish, schinese, japanese, russian, brazilian]

store:
  # Plain text, ~300 characters max.
  shortDescription: >-
    One or two sentences that sell the game.
  # "About This Game" — Steam BBCode: [h2] [b] [i] [u] [list] [*] [olist] [img] [url=...] [hr]
  about: |
    [h2]Your hook here[/h2]
    Describe the game in a few short paragraphs.

    [h2]Features[/h2]
    [list]
    [*] Feature one
    [*] Feature two
    [/list]
  tags: []
  supportedLanguages:
    english: { interface: true, fullAudio: false, subtitles: true }
  systemRequirements:
    windows:
      minimum:
        os: Windows 10 64-bit
        processor: ""
        memory: 4 GB RAM
        graphics: ""
        directx: Version 11
        storage: 2 GB available space
      recommended: {}
  screenshotsDir: store/screenshots      # >= 5 images, 1920x1080 or larger, 16:9. Localized variants: shot1_japanese.png
  art:
    keyArt: store/art/keyart.png          # textless art, ideally 3840x1240 or larger
    logo: store/art/logo.png              # transparent PNG
    overrides: {}                         # e.g. header_capsule: store/art/header_handmade.png

achievements: []
#  - id: ACH_FIRST_WIN                    # API name used in code
#    name: First Blood
#    description: Win your first match.
#    hidden: false
#    icon: achievements/first_win.png     # any size; converted to 256x256 JPG, locked version generated

# cloud:
#   byteQuota: 104857600                  # 100 MB per user
#   fileQuota: 100
#   autoCloud:
#     roots:
#       - root: WinAppDataLocalLow        # App Install Directory, WinMyDocuments, WinAppDataLocal, WinAppDataLocalLow,
#         subdirectory: "Company/Game"    # WinAppDataRoaming, WinSavedGames, MacHome, MacAppSupport, MacDocuments,
#         pattern: "*.sav"                # LinuxHome, LinuxXdgDataHome, SteamCloudDocuments
#         os: windows
#         recursive: true

# app:
#   installFolder: MyGame
#   launchOptions:
#     - executable: MyGame.exe
#       os: windows
#       arch: "64"
`;
}
