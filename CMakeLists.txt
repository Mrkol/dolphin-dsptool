cmake_minimum_required(VERSION 3.24)
project(dolphin-dsptool LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_CXX_SCAN_FOR_MODULES OFF)
# Ship a single executable without the Visual C++ redistributable or a fmt DLL.
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
set(BUILD_SHARED_LIBS OFF)

set(DOLPHIN "${CMAKE_CURRENT_SOURCE_DIR}/dolphin")
if(NOT EXISTS "${DOLPHIN}/Externals/fmt/fmt/CMakeLists.txt")
  message(FATAL_ERROR "Run python scripts/bootstrap.py before configuring.")
endif()
set(FMT_DOC OFF CACHE BOOL "" FORCE)
set(FMT_TEST OFF CACHE BOOL "" FORCE)
set(FMT_INSTALL OFF CACHE BOOL "" FORCE)
add_subdirectory("${DOLPHIN}/Externals/fmt/fmt" EXCLUDE_FROM_ALL)

add_executable(dsptool
  "${DOLPHIN}/Source/DSPTool/DSPTool.cpp"
  "${DOLPHIN}/Source/Core/Core/DSP/DSPAssembler.cpp"
  "${DOLPHIN}/Source/Core/Core/DSP/DSPCodeUtil.cpp"
  "${DOLPHIN}/Source/Core/Core/DSP/DSPDisassembler.cpp"
  "${DOLPHIN}/Source/Core/Core/DSP/DSPTables.cpp"
  "${DOLPHIN}/Source/Core/Core/DSP/LabelMap.cpp"
  "${DOLPHIN}/Source/Core/Common/CommonFuncs.cpp"
  "${DOLPHIN}/Source/Core/Common/DynamicLibrary.cpp"
  "${DOLPHIN}/Source/Core/Common/FileUtil.cpp"
  "${DOLPHIN}/Source/Core/Common/IOFile.cpp"
  "${DOLPHIN}/Source/Core/Common/StringUtil.cpp"
  src/ConsoleHost.cpp
)
target_include_directories(dsptool PRIVATE "${DOLPHIN}/Source/Core")
target_compile_definitions(dsptool PRIVATE DATA_DIR="")
target_link_libraries(dsptool PRIVATE fmt::fmt ${CMAKE_DL_LIBS})
if(MSVC)
  target_compile_options(dsptool PRIVATE /utf-8 /permissive- /Zc:preprocessor)
  target_compile_definitions(dsptool PRIVATE UNICODE _UNICODE NOMINMAX _CRT_SECURE_NO_WARNINGS)
  target_link_libraries(dsptool PRIVATE shell32 ole32 comdlg32 shlwapi)
elseif(APPLE)
  find_package(Iconv REQUIRED)
  find_library(CORE_FOUNDATION CoreFoundation REQUIRED)
  target_link_libraries(dsptool PRIVATE Iconv::Iconv "${CORE_FOUNDATION}")
elseif(CMAKE_SYSTEM_NAME STREQUAL "Linux")
  # Keep glibc dynamic; embed the compiler runtimes for easier redistribution.
  target_link_options(dsptool PRIVATE -static-libgcc -static-libstdc++)
endif()

install(TARGETS dsptool RUNTIME DESTINATION bin)
include(CTest)
if(BUILD_TESTING)
  find_package(Python3 3.10 REQUIRED COMPONENTS Interpreter)
  add_test(NAME dsptool-cli
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/smoke.py"
      "$<TARGET_FILE:dsptool>")
endif()
