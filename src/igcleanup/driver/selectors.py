"""Every Instagram selector and text marker. Strings only; no logic.

When Instagram changes its layout, this is the only file that should need editing.
"""

LOGIN_URL_MARKER = "/accounts/login"
LOGGED_IN_MARKER_ROLE = ("link", "Home")

GONE_TEXT = "Sorry, this page isn't available."
GONE_TITLE = "Page not found"
GONE_TEXT_TAGS = ("span", "p", "h1", "h2", "h3")
RATE_LIMIT_TEXTS = ("Try again later", "We restrict certain activity")
DIALOG = 'div[role="dialog"]'

META_DESCRIPTION = 'meta[property="og:description"]'
META_COUNTS_RE = r"([\d,.]+[KM]?)\s+Followers,\s+([\d,.]+[KM]?)\s+Following,\s+([\d,.]+[KM]?)\s+Posts"
META_TITLE = 'meta[property="og:title"]'

PROFILE_PIC_ALT_SUFFIX = "'s profile picture"

POST_TILE_LINK = 'a[href*="/p/"], a[href*="/reel/"]'
PINNED_ICON_ARIA = "Pinned post icon"
POST_TIME = "time[datetime]"

FOLLOWING_DIALOG = 'div[role="dialog"]'
FOLLOWING_LINK_NAME_RE = r"\bfollowing$"  # accessible name like "39 following"
DIALOG_USER_LINK = 'div[role="dialog"] a[href^="/"]'
DIALOG_USER_LINK_JS = 'a[href^="/"]'

FOLLOW_BUTTON_TEXTS = ("Follow", "Follow Back")
FOLLOWING_BUTTON_TEXTS = ("Following", "Requested")
UNFOLLOW_CONFIRM_TEXT = "Unfollow"
DISMISS_DIALOG_TEXTS = ("Not Now", "Not now")

PROFILE_HEADER = "header"
NAV_PROFILE_LINK = 'a[href^="/"][href$="/"]:has(img[alt$="\'s profile picture"])'
NAV_PROFILE_ROLE = ("link", "Profile")
