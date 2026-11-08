// SPDX-License-Identifier: GPL-2.0-or-later
// Replace Dolphin's application logging and graphical message dialogs only.
#include <cstdio>

#include <fmt/format.h>

#include "Common/Logging/Log.h"
#include "Common/MsgHandler.h"

namespace Common::Log
{
void GenericLogFmtImpl(LogLevel level, LogType, const char* file, int line,
                       fmt::string_view format, const fmt::format_args& args)
{
  if (level <= LogLevel::LWARNING)
    fmt::print(stderr, "{}:{}: {}\n", file, line, fmt::vformat(format, args));
}
}  // namespace Common::Log

namespace Common
{
bool MsgAlertFmtImpl(bool, MsgType, Log::LogType, const char* file, int line,
                     fmt::string_view format, const fmt::format_args& args)
{
  fmt::print(stderr, "{}:{}: {}\n", file, line, fmt::vformat(format, args));
  return false;
}
}  // namespace Common
