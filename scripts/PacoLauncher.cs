using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;
using System.Drawing;
using System.Drawing.Drawing2D;

internal static class PacoLauncher
{
    [STAThread]
    private static void Main()
    {
        string launcherDirectory = AppDomain.CurrentDomain.BaseDirectory;
        string projectRoot = Directory.GetParent(launcherDirectory.TrimEnd(Path.DirectorySeparatorChar)).FullName;
        string python = Path.Combine(projectRoot, ".venv-win", "Scripts", "pythonw.exe");
        string entryPoint = Path.Combine(projectRoot, "run_assistant.py");
        string ollamaStartup = Path.Combine(projectRoot, "scripts", "ensure_ollama.ps1");

        if (!File.Exists(python) || !File.Exists(entryPoint) || !File.Exists(ollamaStartup))
        {
            MessageBox.Show(
                "Paco launcher could not find the app runtime.",
                "Paco",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
            return;
        }

        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new StartupIndicator(projectRoot, python, entryPoint, ollamaStartup));
    }

    private sealed class StartupIndicator : Form
    {
        private const int RuntimeStartupTimeoutMilliseconds = 45000;
        private const int WindowStartupTimeoutMilliseconds = 45000;
        private const int LoadingWindowSize = 82;
        private const int HandoffFadeMilliseconds = 180;
        private static readonly Color[] LoadingGradient =
        {
            Color.FromArgb(112, 255, 184),
            Color.FromArgb(54, 226, 136),
            Color.FromArgb(22, 145, 82),
            Color.FromArgb(44, 235, 139),
            Color.FromArgb(112, 255, 184)
        };

        private readonly string projectRoot;
        private readonly string python;
        private readonly string entryPoint;
        private readonly string ollamaStartup;
        private readonly Timer animationTimer;
        private readonly Timer runtimeStartupTimer;
        private readonly Timer windowActivationTimer;
        private readonly StringBuilder ollamaError = new StringBuilder();
        private Process pacoProcess;
        private Process ollamaProcess;
        private System.Threading.EventWaitHandle startupReadyEvent;
        private int activationAttempts;
        private float angle;
        private readonly long ringStartedAtMilliseconds;
        private int handoffFadeElapsedMilliseconds;
        private bool handoffStarted;
        private bool errorShown;

        internal StartupIndicator(string projectRoot, string python, string entryPoint, string ollamaStartup)
        {
            this.projectRoot = projectRoot;
            this.python = python;
            this.entryPoint = entryPoint;
            this.ollamaStartup = ollamaStartup;
            ringStartedAtMilliseconds = UnixTimeMilliseconds();

            AutoScaleMode = AutoScaleMode.None;
            BackColor = Color.FromArgb(9, 16, 13);
            ClientSize = new Size(LoadingWindowSize, LoadingWindowSize);
            DoubleBuffered = true;
            FormBorderStyle = FormBorderStyle.None;
            MaximizeBox = false;
            MinimizeBox = false;
            Opacity = 0.92;
            ShowIcon = false;
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.Manual;
            TopMost = true;

            animationTimer = new Timer { Interval = 16 };
            animationTimer.Tick += delegate
            {
                angle = (float)(((UnixTimeMilliseconds() - ringStartedAtMilliseconds) * (5.5 / 16.0)) % 360.0);
                if (handoffStarted)
                {
                    handoffFadeElapsedMilliseconds += animationTimer.Interval;
                    Opacity = 0.92 * Math.Max(
                        0.0,
                        1.0 - (handoffFadeElapsedMilliseconds / (double)HandoffFadeMilliseconds)
                    );
                    if (handoffFadeElapsedMilliseconds >= HandoffFadeMilliseconds)
                    {
                        Close();
                        return;
                    }
                }
                Invalidate();
            };

            runtimeStartupTimer = new Timer { Interval = RuntimeStartupTimeoutMilliseconds };
            runtimeStartupTimer.Tick += RuntimeStartupTimedOut;

            windowActivationTimer = new Timer { Interval = 75 };
            windowActivationTimer.Tick += ActivatePacoWindow;
        }

        protected override void OnLoad(EventArgs eventArgs)
        {
            base.OnLoad(eventArgs);
            Rectangle workingArea = Screen.FromPoint(Cursor.Position).WorkingArea;
            Location = new Point(
                workingArea.Left + ((workingArea.Width - ClientRectangle.Width) / 2),
                workingArea.Top + ((workingArea.Height - ClientRectangle.Height) / 2)
            );
        }

        protected override void OnShown(EventArgs eventArgs)
        {
            base.OnShown(eventArgs);
            Rectangle loadingBounds = LoadingBounds();
            using (GraphicsPath path = RoundedRectangle(loadingBounds, 16))
            {
                Region = new Region(path);
            }
            animationTimer.Start();
            BeginInvoke((MethodInvoker)StartRuntime);
        }

        protected override void OnPaint(PaintEventArgs eventArgs)
        {
            base.OnPaint(eventArgs);
            Graphics graphics = eventArgs.Graphics;
            graphics.SmoothingMode = SmoothingMode.AntiAlias;

            Rectangle loadingBounds = LoadingBounds();
            RectangleF ring = new RectangleF(loadingBounds.Left + 23f, 23f, 36f, 36f);
            const int segmentCount = 64;
            const float segmentSweep = 360f / segmentCount;
            for (int index = 0; index < segmentCount; index++)
            {
                float position = index / (float)segmentCount;
                using (Pen segment = new Pen(GradientColor(position), 8f))
                {
                    segment.StartCap = LineCap.Flat;
                    segment.EndCap = LineCap.Flat;
                    graphics.DrawArc(
                        segment,
                        ring,
                        angle + (index * segmentSweep),
                        segmentSweep + 1.2f
                    );
                }
            }
        }

        private static Color GradientColor(float position)
        {
            float scaled = position * (LoadingGradient.Length - 1);
            int startIndex = Math.Min((int)scaled, LoadingGradient.Length - 2);
            float blend = scaled - startIndex;
            Color start = LoadingGradient[startIndex];
            Color end = LoadingGradient[startIndex + 1];
            return Color.FromArgb(
                start.A + (int)((end.A - start.A) * blend),
                start.R + (int)((end.R - start.R) * blend),
                start.G + (int)((end.G - start.G) * blend),
                start.B + (int)((end.B - start.B) * blend)
            );
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                animationTimer.Dispose();
                runtimeStartupTimer.Dispose();
                windowActivationTimer.Dispose();
                if (ollamaProcess != null)
                {
                    ollamaProcess.Dispose();
                }
                if (pacoProcess != null)
                {
                    pacoProcess.Dispose();
                }
                if (startupReadyEvent != null)
                {
                    startupReadyEvent.Dispose();
                }
            }
            base.Dispose(disposing);
        }

        private void StartRuntime()
        {
            ollamaProcess = new Process
            {
                StartInfo = new ProcessStartInfo
                {
                    FileName = "powershell.exe",
                    Arguments = "-NoProfile -ExecutionPolicy Bypass -File \"" + ollamaStartup + "\"",
                    WorkingDirectory = projectRoot,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardError = true
                },
                EnableRaisingEvents = true
            };
            ollamaProcess.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs eventArgs)
            {
                if (!string.IsNullOrWhiteSpace(eventArgs.Data))
                {
                    ollamaError.AppendLine(eventArgs.Data);
                }
            };
            ollamaProcess.Exited += delegate
            {
                if (!IsDisposed && IsHandleCreated)
                {
                    BeginInvoke((MethodInvoker)FinishRuntimeStart);
                }
            };

            try
            {
                ollamaProcess.Start();
                ollamaProcess.BeginErrorReadLine();
                runtimeStartupTimer.Start();
            }
            catch (Exception exception)
            {
                ShowStartupError("Paco could not start its local runtime.\n\n" + exception.Message);
            }
        }

        private void FinishRuntimeStart()
        {
            runtimeStartupTimer.Stop();
            if (errorShown || ollamaProcess == null)
            {
                return;
            }

            ollamaProcess.WaitForExit();
            int exitCode = ollamaProcess.ExitCode;
            ollamaProcess.Dispose();
            ollamaProcess = null;
            if (exitCode != 0)
            {
                ShowStartupError("Ollama could not be started.\n\n" + ollamaError.ToString().Trim());
                return;
            }

            try
            {
                string startupEventName = "PacoStartupReady_" + Process.GetCurrentProcess().Id + "_" + Guid.NewGuid().ToString("N");
                startupReadyEvent = new System.Threading.EventWaitHandle(
                    false,
                    System.Threading.EventResetMode.ManualReset,
                    startupEventName
                );
                ProcessStartInfo startInfo = new ProcessStartInfo
                {
                    FileName = python,
                    Arguments = "\"" + entryPoint + "\"",
                    WorkingDirectory = projectRoot,
                    UseShellExecute = false,
                    CreateNoWindow = true
                };
                startInfo.EnvironmentVariables["PACO_STARTUP_EVENT"] = startupEventName;
                startInfo.EnvironmentVariables["PACO_STARTUP_RING_STARTED_MS"] = ringStartedAtMilliseconds.ToString();
                Rectangle loadingBounds = LoadingBounds();
                startInfo.EnvironmentVariables["PACO_STARTUP_RING_CENTER_X"] = (Left + loadingBounds.Left + (LoadingWindowSize / 2)).ToString();
                startInfo.EnvironmentVariables["PACO_STARTUP_RING_CENTER_Y"] = (Top + loadingBounds.Top + (LoadingWindowSize / 2)).ToString();
                pacoProcess = Process.Start(startInfo);
                AllowSetForegroundWindow(pacoProcess.Id);
                activationAttempts = 0;
                windowActivationTimer.Start();
            }
            catch (Exception exception)
            {
                ShowStartupError("Paco could not open.\n\n" + exception.Message);
            }
        }

        private void ActivatePacoWindow(object sender, EventArgs eventArgs)
        {
            activationAttempts++;
            if (pacoProcess == null || startupReadyEvent == null)
            {
                ShowStartupError("Paco startup state was lost before its window opened.");
                return;
            }
            pacoProcess.Refresh();

            if (startupReadyEvent.WaitOne(0))
            {
                windowActivationTimer.Stop();
                handoffStarted = true;
                handoffFadeElapsedMilliseconds = 0;
                return;
            }

            if (pacoProcess.HasExited)
            {
                ShowStartupError("Paco closed before its window opened.");
                return;
            }

            int timeoutAttempts = WindowStartupTimeoutMilliseconds / windowActivationTimer.Interval;
            if (activationAttempts >= timeoutAttempts)
            {
                StopProcess(pacoProcess);
                ShowStartupError("Paco did not finish opening within 45 seconds. The stalled startup was stopped; try opening Paco again.");
            }
        }

        private void RuntimeStartupTimedOut(object sender, EventArgs eventArgs)
        {
            runtimeStartupTimer.Stop();
            StopProcess(ollamaProcess);
            ShowStartupError("The local runtime did not respond within 45 seconds. The stalled startup was stopped; check Ollama and try again.");
        }

        private static void StopProcess(Process process)
        {
            if (process == null)
            {
                return;
            }

            try
            {
                process.Refresh();
                if (!process.HasExited)
                {
                    process.Kill();
                }
            }
            catch (InvalidOperationException)
            {
            }
            catch (System.ComponentModel.Win32Exception)
            {
            }
        }

        private void ShowStartupError(string message)
        {
            if (errorShown)
            {
                return;
            }
            errorShown = true;
            animationTimer.Stop();
            runtimeStartupTimer.Stop();
            windowActivationTimer.Stop();
            Hide();
            MessageBox.Show(message, "Paco", MessageBoxButtons.OK, MessageBoxIcon.Error);
            Close();
        }

        private static GraphicsPath RoundedRectangle(Rectangle bounds, int radius)
        {
            int diameter = radius * 2;
            GraphicsPath path = new GraphicsPath();
            path.AddArc(bounds.Left, bounds.Top, diameter, diameter, 180, 90);
            path.AddArc(bounds.Right - diameter, bounds.Top, diameter, diameter, 270, 90);
            path.AddArc(bounds.Right - diameter, bounds.Bottom - diameter, diameter, diameter, 0, 90);
            path.AddArc(bounds.Left, bounds.Bottom - diameter, diameter, diameter, 90, 90);
            path.CloseFigure();
            return path;
        }

        private Rectangle LoadingBounds()
        {
            return new Rectangle(
                Math.Max(0, (ClientRectangle.Width - LoadingWindowSize) / 2),
                Math.Max(0, (ClientRectangle.Height - LoadingWindowSize) / 2),
                LoadingWindowSize,
                LoadingWindowSize
            );
        }

        private static long UnixTimeMilliseconds()
        {
            return (long)(DateTime.UtcNow - new DateTime(1970, 1, 1)).TotalMilliseconds;
        }

        [DllImport("user32.dll")]
        private static extern bool AllowSetForegroundWindow(int processId);
    }
}
