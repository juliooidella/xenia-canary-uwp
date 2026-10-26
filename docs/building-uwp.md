# Building the UWP / Xbox application

Use Visual Studio 2022 with the C++ UWP tools, a Windows 10/11 SDK, CMake 3.21
or newer, and Python 3.10 or newer. The frontend targets x64 and Windows
10 version 1803 (17134) or newer. SDK C++/WinRT projections are used directly.

From the repository root:

```powershell
git submodule update --init --recursive
cmake --preset uwp
cmake --build --preset uwp-release -- /m:3 /p:CL_MPCount=2
```

The UWP solution is `build/uwp/xenia.sln`, with `xenia-uwp` as its startup
project. The executable and package outputs are beneath `build/uwp`. Keep
desktop configurations in another build directory because every UWP library
uses the app-container API partition.

The package includes the Blades frontend assets and the shared netplay dialogs.
Networking capabilities cover internet servers and private LAN connections.
The UWP HTTPS backend uses wolfSSL with the Windows certificate store and
BCrypt randomness; desktop builds retain Schannel. Desktop Discord IPC and USB
portal drivers are unavailable in the app container.

The packaged wolfSSL dependency uses its upstream GPLv2-or-later license;
its license text is included under `Licenses/wolfSSL/COPYING`.

Package signing is disabled by default so compilation does not depend on a
personal signing certificate. Configure your own matching package identity and
certificate in Visual Studio when creating a package for installation.

After installing, verify controller navigation, launching and returning from a
game, profile dialogs, and a multiplayer session on the intended Xbox or UWP
device. A host compilation alone does not validate those device interactions.

## Continuous integration

`.github/workflows/UWP_appx.yml` builds the package on `windows-2022` with the
same commands as above. It runs on pushes to `xsx-devmode` and `uwp-ci` and on
manual dispatch. The job:

- checks with `vswhere` that the Visual Studio 2022 instance has
  `Microsoft.VisualStudio.Workload.Universal` and
  `Microsoft.VisualStudio.ComponentGroup.UWP.VC`, and installs missing ones
  with `vs_installer.exe modify`. It logs the installed Windows SDKs and the
  free disk space;
- fetches every submodule except `third_party/xbyak_aarch64`, matching the
  desktop workflow. The Vulkan SDK is not needed because the UWP build skips
  the Vulkan backends (`src/xenia/CMakeLists.txt`);
- creates a self-signed certificate whose subject equals the manifest
  `Publisher` and passes its thumbprint as `XENIA_UWP_SIGN_SHA1`, so the
  `xenia-uwp-release` target signs `build/uwp/release/Xenia-Netplay-<version>.appx`;
- fails if the package is smaller than 50 MB, unsigned, or has a different
  identity from `Package.appxmanifest`, and prints the packaged
  `AppxManifest.xml`. Visual Studio adds the `Microsoft.VCLibs.140.00`
  dependency there; the source manifest does not list it;
- uploads the `.appx` with the matching `.cer`, plus a separate log artifact
  (CMake configure output, MSBuild errors and warnings, `CMakeCache.txt`).

To build your own package with the same signing step, create a certificate
whose subject equals the manifest `Publisher`, then configure with
`cmake --preset uwp -DXENIA_UWP_SIGN_SHA1=<thumbprint>`.

Installing over an existing package requires a higher `Identity Version` with
the same `Name` and `Publisher`. The package family name depends only on these
two values, not on the certificate.
