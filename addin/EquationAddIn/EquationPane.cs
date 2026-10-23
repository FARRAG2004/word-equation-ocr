using System;
using System.Drawing;
using System.Linq;
using System.Threading;
using System.Windows.Forms;
using Word = Microsoft.Office.Interop.Word;

namespace EquationAddIn
{
    /// <summary>Side panel: start buttons, live progress with time left and Cancel, and a results list.</summary>
    internal class EquationPane : UserControl
    {
        static readonly Color Accent = Color.FromArgb(43, 87, 154);  // Word blue
        static readonly Color Ok = Color.FromArgb(16, 124, 16), Warn = Color.FromArgb(196, 89, 17), Muted = Color.FromArgb(96, 96, 96);

        readonly Button convertAll, convertSel, cancel;
        readonly Label status, summary;
        readonly ProgressBar bar;
        readonly ListView list;
        CancellationTokenSource cts;
        Converter job;
        Word.Document doc;

        public bool Busy => cts != null;

        public EquationPane()
        {
            Font = new Font("Segoe UI", 9f);
            BackColor = Color.White;
            AutoScaleMode = AutoScaleMode.Dpi;
            Padding = new Padding(12);

            var title = new Label { Text = "Equation OCR", Font = new Font("Segoe UI Semibold", 13f), ForeColor = Accent, AutoSize = true };
            var subtitle = new Label
            {
                Text = "Turns equation pictures into editable Word equations. Runs entirely on this PC.",
                ForeColor = Muted, AutoSize = true, MaximumSize = new Size(280, 0), Margin = new Padding(3, 0, 3, 10),
            };
            convertAll = Primary("Convert all equations");
            convertSel = Secondary("Convert selection only");
            convertAll.Click += (s, e) => Start(selectionOnly: false);
            convertSel.Click += (s, e) => Start(selectionOnly: true);

            status = new Label { Text = "Ready.", AutoSize = true, MaximumSize = new Size(280, 0), Margin = new Padding(3, 12, 3, 4) };
            bar = new ProgressBar { Width = 280, Height = 8, Style = ProgressBarStyle.Continuous, Visible = false };
            cancel = Secondary("Cancel");
            cancel.Visible = false;
            cancel.Click += (s, e) => { cancel.Enabled = false; status.Text = "Cancelling - keeping what is already done..."; cts?.Cancel(); };
            summary = new Label { AutoSize = true, MaximumSize = new Size(280, 0), Margin = new Padding(3, 10, 3, 6), Font = new Font("Segoe UI Semibold", 9.5f) };

            list = new ListView
            {
                View = View.Details, FullRowSelect = true, HeaderStyle = ColumnHeaderStyle.Nonclickable,
                BorderStyle = BorderStyle.FixedSingle, Width = 280, Height = 360, MultiSelect = false, ShowItemToolTips = true,
            };
            list.Columns.Add("#", 40);
            list.Columns.Add("Result", 100);
            list.Columns.Add("Where", 140);
            list.ItemActivate += (s, e) => GoTo();
            list.Click += (s, e) => GoTo();
            var hint = new Label { Text = "Click an item to jump to it. Undo the whole conversion with one Ctrl+Z.", ForeColor = Muted, AutoSize = true, MaximumSize = new Size(280, 0) };

            stack = new FlowLayoutPanel { Dock = DockStyle.Fill, FlowDirection = FlowDirection.TopDown, WrapContents = false, AutoScroll = true };
            stack.Controls.AddRange(new Control[] { title, subtitle, convertAll, convertSel, status, bar, cancel, summary, list, hint });
            stack.Resize += (s, e) => FitWidth();
            Controls.Add(stack);
        }

        readonly FlowLayoutPanel stack;

        /// <summary>Every control follows the panel width, whatever the screen scaling.</summary>
        void FitWidth()
        {
            int w = Math.Max(160, stack.ClientSize.Width - stack.Padding.Horizontal - SystemInformation.VerticalScrollBarWidth - 8);
            foreach (Control c in stack.Controls)
            {
                if (c is Label l) l.MaximumSize = new Size(w, 0);
                else c.Width = w;
            }
            list.Height = Math.Max(160, stack.ClientSize.Height - list.Top - 60);
            int rest = w - list.Columns[0].Width - list.Columns[1].Width - SystemInformation.VerticalScrollBarWidth - 4;
            list.Columns[2].Width = Math.Max(60, rest);
        }

        static Button Primary(string text)
        {
            var b = Secondary(text);
            b.BackColor = Accent; b.ForeColor = Color.White; b.FlatAppearance.BorderColor = Accent;
            b.Font = new Font("Segoe UI Semibold", 9.5f);
            return b;
        }

        static Button Secondary(string text) => new Button
        {
            Text = text, Width = 280, Height = 32, FlatStyle = FlatStyle.Flat, BackColor = Color.White,
            FlatAppearance = { BorderColor = Color.FromArgb(200, 200, 200) }, Cursor = Cursors.Hand, Margin = new Padding(3, 3, 3, 3),
        };

        public async void Start(bool selectionOnly)
        {
            if (Busy) return;
            var app = Globals.ThisAddIn.Application;
            doc = app.ActiveDocument;
            job = new Converter();
            var items = job.Collect(doc, selectionOnly ? app.Selection.Range : null);
            list.Items.Clear();
            summary.Text = "";
            if (items.Count == 0)
            {
                status.Text = selectionOnly ? "No pictures in the selection." : "No pictures found in this document.";
                return;
            }
            foreach (var it in items)
                list.Items.Add(new ListViewItem(new[] { it.Number.ToString(), "waiting", it.Context }) { Tag = it, ForeColor = Muted });

            cts = new CancellationTokenSource();
            SetRunning(true, items.Count);
            job.StatusChanged += s => Ui(() => status.Text = s);
            job.Progress += (done, total, left) => Ui(() =>
            {
                bar.Value = done;
                status.Text = done < total
                    ? $"Reading {done + 1} of {total}" + (left.HasValue ? $"  ·  about {Format(left.Value)} left" : "")
                    : "Finishing...";
            });
            job.ItemDone += it => Ui(() => Show(it));
            try
            {
                await job.ReadAll(cts.Token);
            }
            catch (OperationCanceledException) { }
            catch (Exception ex)
            {
                status.Text = "Stopped: " + ex.Message;
            }
            status.Text = "Updating the document...";
            var (converted, flagged, skipped) = job.Apply(app, doc);
            foreach (ListViewItem li in list.Items) Show((EquationItem)li.Tag);
            int notRead = job.Items.Count(i => i.Status == "pending");
            summary.Text = $"✓ {converted} converted\n⚠ {flagged} to review\n– {skipped} skipped" + (notRead > 0 ? $"\n{notRead} not read (cancelled)" : "");
            summary.ForeColor = flagged > 0 ? Warn : Ok;
            status.Text = cts.IsCancellationRequested ? "Cancelled. Finished equations were converted." : "Done.";
            SetRunning(false, 0);
        }

        void Show(EquationItem it)
        {
            var li = list.Items.Cast<ListViewItem>().First(x => x.Tag == it);
            var (text, color) =
                it.Status == "converted" ? ("✓ Done", Ok) :
                it.Status == "flagged" ? ("⚠ Review", Warn) :
                it.Status == "pending" ? ("waiting", Muted) : ("– Skipped", Muted);
            li.SubItems[1].Text = text;
            li.ForeColor = color;
            li.ToolTipText = string.IsNullOrEmpty(it.Reason) ? it.Context : it.Reason;
        }

        void GoTo()
        {
            if (list.SelectedItems.Count == 0 || Busy) return;
            var it = (EquationItem)list.SelectedItems[0].Tag;
            try
            {
                var target = it.Placed ?? it.Shape?.Range;
                if (target == null) return;
                target.Select();
                Globals.ThisAddIn.Application.ActiveWindow.ScrollIntoView(target);
            }
            catch { }  // the user deleted it meanwhile
        }

        void SetRunning(bool running, int total)
        {
            if (!running) cts = null;
            convertAll.Enabled = convertSel.Enabled = !running;
            bar.Visible = cancel.Visible = running;
            cancel.Enabled = true;
            if (running) { bar.Maximum = total; bar.Value = 0; }
        }

        static string Format(TimeSpan t) => t.TotalMinutes >= 1 ? $"{(int)t.TotalMinutes} min {t.Seconds:00} s" : $"{Math.Max(1, t.Seconds)} s";

        void Ui(Action a)
        {
            if (IsDisposed) return;
            if (InvokeRequired) BeginInvoke(a); else a();
        }
    }
}
