#define NOMINMAX

#include <windows.h>

#include <dxgi1_4.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstring>
#include <cstdlib>
#include <malloc.h>
#include <new>
#include <string>
#include <utility>
#include <vector>

#include <winrt/Windows.ApplicationModel.Activation.h>
#include <winrt/Windows.ApplicationModel.Core.h>
#include <winrt/Windows.ApplicationModel.h>
#include <winrt/Windows.Foundation.Collections.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Storage.Pickers.h>
#include <winrt/Windows.Storage.h>
#include <winrt/Windows.System.h>
#include <winrt/Windows.UI.Composition.h>
#include <winrt/Windows.UI.Core.h>
#include <winrt/Windows.UI.Input.h>
#include <winrt/Windows.UI.ViewManagement.h>
#include <winrt/windows.graphics.display.core.h>

#include <Gamingdeviceinformation.h>

#include "UWPUtil.h"
#include "WinRTKeyboard.h"
#include "XeniaUWP.h"
#include "app_launch_state.h"
#include "protocol_activation.h"
#include "third_party/fmt/include/fmt/format.h"
#include "xenia/base/logging.h"
#include "xenia/config.h"

namespace {

constexpr size_t kTrackedAllocationCount = 4096;
constexpr size_t kTrackedAllocationThreshold = 1ull << 20;
constexpr USHORT kTrackedStackDepth = 8;

struct TrackedAllocation {
  std::atomic<uint64_t> sequence{0};
  size_t size = 0;
  uintptr_t address = 0;
  DWORD thread_id = 0;
  USHORT frame_count = 0;
  uintptr_t frames[kTrackedStackDepth] = {};
};

std::array<TrackedAllocation, kTrackedAllocationCount> tracked_allocations;
std::atomic<uint64_t> tracked_allocation_count{0};

void RecordTrackedAllocation(size_t size, void* address) {
  if (size < kTrackedAllocationThreshold) {
    return;
  }
  uint64_t sequence =
      tracked_allocation_count.fetch_add(1, std::memory_order_relaxed) + 1;
  TrackedAllocation& event =
      tracked_allocations[(sequence - 1) % kTrackedAllocationCount];
  event.sequence.store(0, std::memory_order_release);
  event.size = size;
  event.address = reinterpret_cast<uintptr_t>(address);
  event.thread_id = GetCurrentThreadId();
  void* frames[kTrackedStackDepth] = {};
  event.frame_count = CaptureStackBackTrace(
      2, kTrackedStackDepth, frames, nullptr);
  for (USHORT i = 0; i < event.frame_count; ++i) {
    event.frames[i] = reinterpret_cast<uintptr_t>(frames[i]);
  }
  event.sequence.store(sequence, std::memory_order_release);
}

void LogTrackedAllocations(uint64_t& last_logged_sequence) {
  uint64_t last_sequence =
      tracked_allocation_count.load(std::memory_order_acquire);
  uint64_t first_sequence = last_logged_sequence + 1;
  if (last_sequence >= kTrackedAllocationCount &&
      first_sequence <= last_sequence - kTrackedAllocationCount) {
    XELOGW("Large C++ allocation trace dropped {} events",
           last_sequence - kTrackedAllocationCount - first_sequence + 1);
    first_sequence = last_sequence - kTrackedAllocationCount + 1;
  }
  HMODULE executable = GetModuleHandleW(nullptr);
  uintptr_t image_base = reinterpret_cast<uintptr_t>(executable);
  auto* dos_header = reinterpret_cast<const IMAGE_DOS_HEADER*>(executable);
  auto* nt_headers = reinterpret_cast<const IMAGE_NT_HEADERS*>(
      image_base + dos_header->e_lfanew);
  uintptr_t image_end = image_base + nt_headers->OptionalHeader.SizeOfImage;
  for (uint64_t sequence = first_sequence; sequence <= last_sequence;
       ++sequence) {
    const TrackedAllocation& event =
        tracked_allocations[(sequence - 1) % kTrackedAllocationCount];
    if (event.sequence.load(std::memory_order_acquire) != sequence) {
      continue;
    }
    std::string stack;
    for (USHORT i = 0; i < event.frame_count; ++i) {
      uintptr_t frame = event.frames[i];
      if (!stack.empty()) {
        stack += ',';
      }
      if (frame >= image_base && frame < image_end) {
        stack += fmt::format("+{:X}", frame - image_base);
      } else {
        stack += fmt::format("{:016X}", frame);
      }
    }
    XELOGI("Large C++ allocation #{}: {} KiB at {:016X}, thread {}, stack [{}]",
           sequence, event.size >> 10, event.address, event.thread_id, stack);
  }
  last_logged_sequence = last_sequence;
}

// GPU memory charged to this process on the first adapter; 0 if unknown.
uint64_t QueryGpuMemoryUsage() {
  static IDXGIAdapter3* adapter = nullptr;
  static bool initialized = false;
  if (!initialized) {
    initialized = true;
    IDXGIFactory2* factory = nullptr;
    if (SUCCEEDED(CreateDXGIFactory2(0, IID_PPV_ARGS(&factory)))) {
      IDXGIAdapter1* adapter1 = nullptr;
      if (SUCCEEDED(factory->EnumAdapters1(0, &adapter1))) {
        adapter1->QueryInterface(IID_PPV_ARGS(&adapter));
        adapter1->Release();
      }
      factory->Release();
    }
  }
  if (!adapter) {
    return 0;
  }
  DXGI_QUERY_VIDEO_MEMORY_INFO info = {};
  if (FAILED(adapter->QueryVideoMemoryInfo(
          0, DXGI_MEMORY_SEGMENT_GROUP_LOCAL, &info))) {
    return 0;
  }
  return info.CurrentUsage;
}

// Copies memory that another thread may release meanwhile.
bool TryCopyMemory(void* dest, const void* source, size_t size) {
  __try {
    memcpy(dest, source, size);
    return true;
  } __except (EXCEPTION_EXECUTE_HANDLER) {
    return false;
  }
}

// Logs where the private commit of this process sits: totals per allocation
// size and the largest allocations, with their first bytes to tell the data.
void LogPrivateAllocations(uint64_t app_usage) {
  struct Allocation {
    uintptr_t base;
    uintptr_t first_committed;
    size_t committed;
    DWORD protect;
  };
  std::vector<Allocation> allocations;
  MEMORY_BASIC_INFORMATION info;
  const uint8_t* address = nullptr;
  while (VirtualQuery(address, &info, sizeof(info)) == sizeof(info)) {
    if (info.State == MEM_COMMIT && info.Type == MEM_PRIVATE) {
      uintptr_t base = reinterpret_cast<uintptr_t>(info.AllocationBase);
      if (!allocations.empty() && allocations.back().base == base) {
        allocations.back().committed += info.RegionSize;
      } else {
        allocations.push_back(
            {base, reinterpret_cast<uintptr_t>(info.BaseAddress),
             info.RegionSize, info.Protect});
      }
    }
    const uint8_t* next =
        static_cast<const uint8_t*>(info.BaseAddress) + info.RegionSize;
    if (next <= address) {
      break;
    }
    address = next;
  }

  constexpr size_t kBucketLimits[] = {64ull << 10, 1ull << 20, 16ull << 20,
                                      256ull << 20, SIZE_MAX};
  size_t bucket_count[5] = {};
  size_t bucket_bytes[5] = {};
  size_t total = 0;
  for (const Allocation& allocation : allocations) {
    size_t bucket = 0;
    while (allocation.committed > kBucketLimits[bucket]) {
      ++bucket;
    }
    ++bucket_count[bucket];
    bucket_bytes[bucket] += allocation.committed;
    total += allocation.committed;
  }
  XELOGI(
      "Private allocations at {} MiB app usage: {} MiB in {} allocations; "
      "<=64K {}x {} MiB, <=1M {}x {} MiB, <=16M {}x {} MiB, <=256M {}x {} "
      "MiB, >256M {}x {} MiB",
      app_usage >> 20, total >> 20, allocations.size(), bucket_count[0],
      bucket_bytes[0] >> 20, bucket_count[1], bucket_bytes[1] >> 20,
      bucket_count[2], bucket_bytes[2] >> 20, bucket_count[3],
      bucket_bytes[3] >> 20, bucket_count[4], bucket_bytes[4] >> 20);

  std::sort(allocations.begin(), allocations.end(),
            [](const Allocation& a, const Allocation& b) {
              return a.committed > b.committed;
            });
  constexpr DWORD kReadable = PAGE_READONLY | PAGE_READWRITE | PAGE_WRITECOPY |
                              PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE |
                              PAGE_EXECUTE_WRITECOPY;
  for (size_t i = 0; i < std::min<size_t>(allocations.size(), 12); ++i) {
    const Allocation& allocation = allocations[i];
    std::string bytes;
    uint8_t data[32];
    if ((allocation.protect & kReadable) &&
        !(allocation.protect & (PAGE_GUARD | PAGE_NOACCESS)) &&
        TryCopyMemory(data,
                      reinterpret_cast<const void*>(allocation.first_committed),
                      sizeof(data))) {
      for (size_t j = 0; j < 32; ++j) {
        bytes += fmt::format("{:02X}", data[j]);
      }
      bytes += ' ';
      for (size_t j = 0; j < 32; ++j) {
        bytes += (data[j] >= 0x20 && data[j] < 0x7F) ? char(data[j]) : '.';
      }
    }
    XELOGI("  private allocation {:016X}: {} MiB, protect {:X}, {}",
           allocation.base, allocation.committed >> 20, allocation.protect,
           bytes);
  }
}

}  // namespace

void* operator new(std::size_t size) {
  if (void* allocation = std::malloc(size ? size : 1)) {
    RecordTrackedAllocation(size, allocation);
    return allocation;
  }
  throw std::bad_alloc();
}

void* operator new[](std::size_t size) {
  if (void* allocation = std::malloc(size ? size : 1)) {
    RecordTrackedAllocation(size, allocation);
    return allocation;
  }
  throw std::bad_alloc();
}

void operator delete(void* allocation) noexcept { std::free(allocation); }

void operator delete[](void* allocation) noexcept { std::free(allocation); }

void operator delete(void* allocation, std::size_t) noexcept {
  std::free(allocation);
}

void operator delete[](void* allocation, std::size_t) noexcept {
  std::free(allocation);
}

void* operator new(std::size_t size, std::align_val_t alignment) {
  if (void* allocation = _aligned_malloc(size ? size : 1,
                                         static_cast<size_t>(alignment))) {
    RecordTrackedAllocation(size, allocation);
    return allocation;
  }
  throw std::bad_alloc();
}

void* operator new[](std::size_t size, std::align_val_t alignment) {
  if (void* allocation = _aligned_malloc(size ? size : 1,
                                         static_cast<size_t>(alignment))) {
    RecordTrackedAllocation(size, allocation);
    return allocation;
  }
  throw std::bad_alloc();
}

void operator delete(void* allocation, std::align_val_t) noexcept {
  _aligned_free(allocation);
}

void operator delete[](void* allocation, std::align_val_t) noexcept {
  _aligned_free(allocation);
}

void operator delete(void* allocation, std::size_t,
                     std::align_val_t) noexcept {
  _aligned_free(allocation);
}

void operator delete[](void* allocation, std::size_t,
                       std::align_val_t) noexcept {
  _aligned_free(allocation);
}
#include "xenia/ui/imgui_dialog.h"

using namespace winrt;

using namespace winrt::Windows;
using namespace winrt::Windows::ApplicationModel::Core;
using namespace winrt::Windows::Foundation::Numerics;
using namespace winrt::Windows::UI;
using namespace winrt::Windows::UI::Core;
using namespace winrt::Windows::UI::Composition;
using namespace winrt::Windows::Storage;
using namespace winrt::Windows::Storage::Pickers;

namespace {
UWP::AppLaunchState launch_state;
std::string restart_activation_arguments;
constexpr auto kRestartContext = L"TitleRestartContext";

winrt::fire_and_forget RestartTitleAsync(xe::ui::ImGuiDrawer* drawer,
                                         winrt::hstring arguments) {
  try {
    auto reason = co_await CoreApplication::RequestRestartAsync(arguments);
    if (reason == AppRestartFailureReason::RestartPending) {
      XELOGI("UWP title restart is pending");
      xe::FlushLog();
      co_return;
    }
    XELOGE("UWP title restart rejected: {}", static_cast<int>(reason));
  } catch (const winrt::hresult_error& error) {
    XELOGE("UWP title restart failed: 0x{:08X}",
           static_cast<uint32_t>(error.code().value));
  }
  xe::FlushLog();
  if (drawer) {
    xe::ui::ImGuiDialog::ShowMessageBox(
        drawer, "Unable to restart Xenia",
        "The game restart is saved, but Xbox could not restart Xenia. "
        "Close and reopen Xenia to continue.");
  }
}

winrt::fire_and_forget ReturnToFrontendAsync(xe::ui::ImGuiDrawer* drawer,
                                            std::string return_uri) {
  try {
    const bool accepted = co_await winrt::Windows::System::Launcher::LaunchUriAsync(
        winrt::Windows::Foundation::Uri{winrt::to_hstring(return_uri)});
    XELOGI("UWP in-process frontend handoff accepted: {}", accepted);
    xe::FlushLog();
    if (accepted) co_return;
  } catch (const winrt::hresult_error& error) {
    XELOGE("UWP in-process frontend handoff failed: 0x{:08X}",
           static_cast<uint32_t>(error.code().value));
    xe::FlushLog();
  }
  if (drawer) {
    xe::ui::ImGuiDialog::ShowMessageBox(drawer, "Unable to open frontend",
                                      "The game has closed. Open the frontend manually.");
  }
}

}  // namespace

void UWP::RequestTitleRestart(xe::ui::ImGuiDrawer* drawer,
                              bool launch_data_saved) {
  if (launch_state.restarting()) {
    return;
  }
  if (launch_data_saved) {
    try {
      auto arguments = winrt::to_hstring(launch_state.RestartArguments());
      ApplicationData::Current().LocalSettings().Values().Insert(
          kRestartContext, winrt::box_value(arguments));
      config::SaveConfig();
      launch_state.BeginTitleRestart();
      XELOGI("UWP requesting app restart for saved title; frontend return: {}",
             !launch_state.return_uri().empty());
      xe::FlushLog();
      RestartTitleAsync(drawer, std::move(arguments));
      return;
    } catch (const winrt::hresult_error& error) {
      XELOGE("UWP could not save restart context: 0x{:08X}",
             static_cast<uint32_t>(error.code().value));
    }
  }
  if (drawer) {
    xe::ui::ImGuiDialog::ShowMessageBox(
        drawer, "Unable to save game restart",
        "The restart request could not be saved. Check that Xenia's storage "
        "location is writable, then reopen the game.");
  }
}

void UWP::RestoreTitleRestart(bool has_title) {
  std::string arguments = std::move(restart_activation_arguments);
  try {
    auto values = ApplicationData::Current().LocalSettings().Values();
    if (values.HasKey(kRestartContext)) {
      arguments = winrt::to_string(
          winrt::unbox_value<winrt::hstring>(values.Lookup(kRestartContext)));
      values.Remove(kRestartContext);
    }
  } catch (const winrt::hresult_error& error) {
    XELOGW("UWP could not read restart context: 0x{:08X}",
           static_cast<uint32_t>(error.code().value));
  }
  if (launch_state.RestoreTitleRestart(has_title, arguments)) {
    XELOGI("UWP restored title restart context; frontend return: {}",
           !launch_state.return_uri().empty());
  }
}

bool UWP::ShouldReturnToFrontend() {
  return launch_state.ShouldReturnToFrontend();
}

void UWP::ReturnToFrontend(xe::ui::ImGuiDrawer* drawer) {
  auto return_uri = launch_state.TakeFrontendReturnUri();
  if (!return_uri.empty()) {
    ReturnToFrontendAsync(drawer, std::move(return_uri));
  }
}

struct App : implements<App, IFrameworkViewSource, IFrameworkView> {
  CompositionTarget m_target{nullptr};
  VisualCollection m_visuals{nullptr};
  Visual m_selected{nullptr};
  float2 m_offset{};

  IFrameworkView CreateView() { return *this; }

  void Initialize(CoreApplicationView const& v) {
    v.Activated({this, &App::OnActivate});
  }

  void Load(hstring const&) {}

  void Uninitialize() {}

  void Run() {
    auto navigation =
        winrt::Windows::UI::Core::SystemNavigationManager::GetForCurrentView();

    // UWP on Xbox One triggers a back request whenever the B button is
    // pressed which can result in the app being suspended if unhandled
    navigation.BackRequested(
        [](const winrt::Windows::Foundation::IInspectable&,
           const BackRequestedEventArgs& args) { args.Handled(true); });

    winrt::Windows::Graphics::Display::DisplayInformation displayInfo =
        winrt::Windows::Graphics::Display::DisplayInformation::
            GetForCurrentView();
    if (displayInfo) {
      UWP::SetDPI(displayInfo.LogicalDpi());
    }

    UWP::StartXenia();

    try {
      XELOGI(
          "UWP app memory usage: {} MiB; limit: {} MiB",
          winrt::Windows::System::MemoryManager::AppMemoryUsage() >> 20,
          winrt::Windows::System::MemoryManager::AppMemoryUsageLimit() >> 20);
    } catch (const winrt::hresult_error& error) {
      XELOGW("Unable to query UWP app memory limit: 0x{:08X}",
             static_cast<uint32_t>(error.code().value));
    }

    // Log the app memory usage while it moves, to see how much of the
    // foreground limit a title uses before an allocation fails.
    constexpr uint64_t kMemoryLogStep = 64ull << 20;
    auto next_memory_check = std::chrono::steady_clock::now();
    uint64_t logged_memory_usage = 0;
    uint64_t peak_memory_usage = 0;
    bool memory_usage_available = true;
    uint64_t next_allocation_report = 2ull << 30;
    uint64_t last_logged_allocation_sequence = 0;

    while (!UWP::HasQuit()) {
      CoreWindow::GetForCurrentThread().Dispatcher().ProcessEvents(
          CoreProcessEventsOption::ProcessAllIfPresent);

      UWP::ExecutePendingFunctionsFromUIThread();

      auto now = std::chrono::steady_clock::now();
      if (memory_usage_available && now >= next_memory_check) {
        next_memory_check = now + std::chrono::seconds(2);
        try {
          uint64_t usage =
              winrt::Windows::System::MemoryManager::AppMemoryUsage();
          peak_memory_usage = std::max(peak_memory_usage, usage);
          uint64_t delta = usage > logged_memory_usage
                               ? usage - logged_memory_usage
                               : logged_memory_usage - usage;
          if (usage >= next_allocation_report) {
            LogPrivateAllocations(usage);
            LogTrackedAllocations(last_logged_allocation_sequence);
            next_allocation_report += usage < (4ull << 30) ? (1ull << 30)
                                                           : (512ull << 20);
          }
          if (delta >= kMemoryLogStep) {
            logged_memory_usage = usage;
            uint64_t private_commit = winrt::Windows::System::MemoryManager::
                                          GetAppMemoryReport()
                                              .PrivateCommitUsage();
            XELOGI(
                "UWP app memory usage: {} MiB (peak {} MiB; private commit {} "
                "MiB; GPU {} MiB)",
                usage >> 20, peak_memory_usage >> 20, private_commit >> 20,
                QueryGpuMemoryUsage() >> 20);
          }
        } catch (const winrt::hresult_error&) {
          memory_usage_available = false;
        }
      }

      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    if (peak_memory_usage) {
      XELOGI("UWP app memory usage peak: {} MiB", peak_memory_usage >> 20);
    }

    if (UWP::ShouldReturnToFrontend()) {
      try {
        XELOGI("UWP returning to launch frontend");
        auto operation = winrt::Windows::System::Launcher::LaunchUriAsync(
            winrt::Windows::Foundation::Uri{
                winrt::to_hstring(launch_state.return_uri())});
        // Keep the UI apartment responsive until the frontend handoff
        // completes, rather than exiting before Windows handles the URI.
        while (operation.Status() ==
               winrt::Windows::Foundation::AsyncStatus::Started) {
          CoreWindow::GetForCurrentThread().Dispatcher().ProcessEvents(
              CoreProcessEventsOption::ProcessAllIfPresent);
          std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        XELOGI("UWP launch frontend handoff accepted: {}", operation.GetResults());
      } catch (const winrt::hresult_error& error) {
        XELOGE("UWP launch frontend handoff failed: 0x{:08X}",
               static_cast<uint32_t>(error.code().value));
      }
    }
    // The upstream application teardown ends the process, so complete any
    // requested frontend handoff before invoking it.
    UWP::ShutdownXenia();
    CoreApplication::Exit();
  }

  void OnActivate(
      const winrt::Windows::ApplicationModel::Core::CoreApplicationView& v,
      const winrt::Windows::ApplicationModel::Activation::IActivatedEventArgs&
          args) {
    if (args.Kind() == winrt::Windows::ApplicationModel::Activation::
                           ActivationKind::Protocol) {
      auto protocol_args =
          args.as<winrt::Windows::ApplicationModel::Activation::
                      ProtocolActivatedEventArgs>();
      auto query = protocol_args.Uri().QueryParsed();
      std::vector<std::pair<std::string, std::string>> arguments;
      arguments.reserve(query.Size());
      for (const auto& argument : query) {
        arguments.emplace_back(winrt::to_string(argument.Name()),
                               winrt::to_string(argument.Value()));
      }
      auto activation = UWP::ParseProtocolActivation(arguments);
      launch_state.ActivateProtocol(std::move(activation.launch_on_exit));
      UWP::SetAutomaticLaunch(activation.game_path.value_or(std::string{}));
    } else if (args.Kind() == winrt::Windows::ApplicationModel::Activation::
                                  ActivationKind::Launch) {
      restart_activation_arguments =
          winrt::to_string(args.as<winrt::Windows::ApplicationModel::
                                       Activation::LaunchActivatedEventArgs>()
                               .Arguments());
    }

    auto appView = winrt::Windows::UI::ViewManagement::ApplicationView::
        GetForCurrentView();
    appView.PreferredLaunchWindowingMode(
        winrt::Windows::UI::ViewManagement::ApplicationViewWindowingMode::
            FullScreen);

    CoreWindow window = CoreWindow::GetForCurrentThread();
    window.Activate();
    appView.TryEnterFullScreenMode();
  }

  void OnCharacterReceived(
      winrt::Windows::UI::Core::CoreWindow const& /* sender */,
      winrt::Windows::UI::Core::CharacterReceivedEventArgs const&
          e /* args */) {
    UWP::HandleCharacter(e.KeyCode());
  }

  void SetWindow(CoreWindow const& window) {
    window.CharacterReceived({this, &App::OnCharacterReceived});
    // Key events feed the caret keys of the ImGui text input.
    window.KeyDown([](CoreWindow const&,
                      winrt::Windows::UI::Core::KeyEventArgs const& e) {
      UWP::HandleKey(static_cast<uint32_t>(e.VirtualKey()), true);
    });
    window.KeyUp([](CoreWindow const&,
                    winrt::Windows::UI::Core::KeyEventArgs const& e) {
      UWP::HandleKey(static_cast<uint32_t>(e.VirtualKey()), false);
    });
    window.Closed([](auto const&, auto const&) { UWP::RequestQuit(); });
  }
};

int __stdcall wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
  winrt::init_apartment();
  CoreApplication::Run(make<App>());
  winrt::uninit_apartment();
}
