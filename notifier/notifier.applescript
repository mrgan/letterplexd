-- Posts a macOS notification under this app bundle's own identity, so alerts
-- read as "Letterplexd" instead of "Script Editor" (osascript's default).
-- Text arrives via the environment to avoid quoting the values into a script.
-- See CLAUDE.md "Staying aware of it" for how to build this.

on run
	set theTitle to utf8Env("LPS_TITLE")
	set theSubtitle to utf8Env("LPS_SUBTITLE")
	set theMessage to utf8Env("LPS_MESSAGE")

	if theTitle is "" then set theTitle to "Letterplexd"

	if theSubtitle is "" then
		display notification theMessage with title theTitle
	else
		display notification theMessage with title theTitle subtitle theSubtitle
	end if
end run

-- `system attribute` decodes the environment as Mac OS Roman, which turns any
-- non-ASCII into mojibake: an arrow becomes ",Üí", and a film title like
-- "La cérémonie" becomes "La c√©r√©monie". Reading the value back out through
-- the shell returns it as the UTF-8 it actually is.
on utf8Env(varName)
	return do shell script "printf '%s' \"$" & varName & "\""
end utf8Env
