-- Posts a macOS notification under this app bundle's own identity, so alerts
-- read as "Letterboxd Sync" instead of "Script Editor" (osascript's default).
-- Text arrives via the environment to avoid quoting the values into a script.
-- See CLAUDE.md "Staying aware of it" for how to build this.

on run
	set theTitle to system attribute "LPS_TITLE"
	set theSubtitle to system attribute "LPS_SUBTITLE"
	set theMessage to system attribute "LPS_MESSAGE"

	if theTitle is "" then set theTitle to "Letterboxd Sync"

	if theSubtitle is "" then
		display notification theMessage with title theTitle
	else
		display notification theMessage with title theTitle subtitle theSubtitle
	end if
end run
