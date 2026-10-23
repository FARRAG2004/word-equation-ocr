using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Xml.Linq;
using Word = Microsoft.Office.Interop.Word;

namespace EquationAddIn
{
    /// <summary>One picture to convert, and what the server decided for it.</summary>
    internal class EquationItem
    {
        public int Number;              // 1-based, in document order
        public string Context;          // nearby text, to recognise it in the list
        public Word.InlineShape Shape;
        public Word.Range Placed;       // where the equation went; Word keeps it up to date as the text changes
        public byte[] Image;
        public string Status = "pending"; // pending | converted | flagged | skipped | error
        public string Reason = "";
        public string Latex = "";
        public string Omml = "";
    }

    /// <summary>
    /// Collects pictures on Word's thread, reads them on a background thread (Word stays responsive),
    /// then applies every change at once inside a single undo step.
    /// </summary>
    internal class Converter
    {
        const string Server = "http://127.0.0.1:8765";
        static readonly XNamespace Pkg = "http://schemas.microsoft.com/office/2006/xmlPackage";
        static readonly XNamespace W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
        static readonly XNamespace WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing";
        static readonly XNamespace A = "http://schemas.openxmlformats.org/drawingml/2006/main";
        static readonly XNamespace R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
        static readonly XNamespace Rel = "http://schemas.openxmlformats.org/package/2006/relationships";
        static readonly HttpClient Http = new HttpClient { Timeout = TimeSpan.FromMinutes(3) };
        static Process serverProcess;

        // Folder holding .venv and server\ : set by install.ps1, or EQUATION_ADDIN_HOME for development.
        static string Home =>
            Environment.GetEnvironmentVariable("EQUATION_ADDIN_HOME")
            ?? Microsoft.Win32.Registry.GetValue(@"HKEY_CURRENT_USER\Software\EquationAddIn", "Home", null) as string
            ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "equation-addin");

        public event Action<string> StatusChanged;            // human-readable status line
        public event Action<int, int, TimeSpan?> Progress;     // done, total, estimated time left
        public event Action<EquationItem> ItemDone;
        public List<EquationItem> Items { get; private set; } = new List<EquationItem>();

        /// <summary>Pictures in the document (or only inside the selection), in document order.</summary>
        public List<EquationItem> Collect(Word.Document doc, Word.Range only = null)
        {
            var all = doc.InlineShapes.Cast<Word.InlineShape>().ToList();
            var images = DocumentImages(doc.WordOpenXML, all.Count);
            Items = new List<EquationItem>();
            for (int k = 0; k < all.Count; k++)
            {
                var s = all[k];
                if (s.Type != Word.WdInlineShapeType.wdInlineShapePicture && s.Type != Word.WdInlineShapeType.wdInlineShapeLinkedPicture)
                    continue;
                if (only != null && (s.Range.Start < only.Start || s.Range.End > only.End))
                    continue;
                Items.Add(new EquationItem
                {
                    Number = Items.Count + 1,
                    Shape = s,
                    Image = images?[k] ?? ImageBytes(s.Range.WordOpenXML),
                    Context = ContextOf(s.Range),
                });
            }
            return Items;
        }

        /// <summary>Background part: start the server if needed and read every picture.</summary>
        public Task ReadAll(CancellationToken cancel) => Task.Run(() =>
        {
            EnsureServer(cancel);
            var watch = Stopwatch.StartNew();
            int done = 0;
            StatusChanged?.Invoke($"Reading 1 of {Items.Count}...");
            foreach (var item in Items)
            {
                if (cancel.IsCancellationRequested) break;
                if (item.Image == null)
                {
                    item.Status = "skipped"; item.Reason = "no readable image data";
                }
                else
                {
                    try
                    {
                        var r = Ocr(item.Image, cancel);
                        item.Status = (string)r["status"];
                        item.Reason = (string)r["reason"];
                        item.Latex = (string)r["latex"];
                        item.Omml = (string)r["omml"];
                    }
                    catch (OperationCanceledException) { break; }
                    catch (Exception ex) { item.Status = "error"; item.Reason = ex.Message; }
                }
                done++;
                var perItem = TimeSpan.FromTicks(watch.Elapsed.Ticks / done);
                Progress?.Invoke(done, Items.Count, done >= 2 ? TimeSpan.FromTicks(perItem.Ticks * (Items.Count - done)) : (TimeSpan?)null);
                ItemDone?.Invoke(item);
            }
        }, cancel);

        /// <summary>Word's thread: apply all results in one undo step. Returns (converted, flagged, skipped).</summary>
        public (int, int, int) Apply(Word.Application app, Word.Document doc)
        {
            int converted = 0, flagged = 0, skipped = 0;
            app.ScreenUpdating = false;
            app.UndoRecord.StartCustomRecord("Convert equation images");
            try
            {
                foreach (var item in Items.AsEnumerable().Reverse())  // last first: earlier positions stay valid
                {
                    try
                    {
                        if (item.Status == "converted")
                        {
                            var range = item.Shape.Range;
                            range.InsertXML(FlatOpc(item.Omml));
                            item.Placed = range;
                            item.Shape = null;
                            converted++;
                        }
                        else if (item.Status == "flagged")
                        {
                            doc.Comments.Add(item.Shape.Range, $"Equation OCR: please review ({item.Reason}).\nLaTeX guess: {item.Latex}");
                            flagged++;
                        }
                        else if (item.Status != "pending") skipped++;
                    }
                    catch (Exception ex)  // e.g. the picture was deleted while it was being read
                    {
                        item.Status = "error"; item.Reason = "could not change the document: " + ex.Message;
                        skipped++;
                    }
                }
            }
            finally
            {
                app.UndoRecord.EndCustomRecord();
                app.ScreenUpdating = true;
            }
            return (converted, flagged, skipped);
        }

        public static void Shutdown()
        {
            try { if (serverProcess != null && !serverProcess.HasExited) serverProcess.Kill(); } catch { }
        }

        static string ContextOf(Word.Range r)
        {
            try
            {
                var para = r.Paragraphs[1].Range;
                var text = Clean(para.Text);
                if (text.Length < 3 && para.Start > 0)  // a picture alone on its line: use the line above
                    text = Clean(para.Previous(Word.WdUnits.wdParagraph, 1)?.Text ?? "");
                return text.Length > 60 ? text.Substring(0, 57) + "..." : text;
            }
            catch { return ""; }
        }

        static string Clean(string s) => new string((s ?? "").Where(c => c >= ' ' && c != '/').ToArray()).Trim();

        static bool ServerAlive()
        {
            try { return Http.GetAsync(Server + "/health").GetAwaiter().GetResult().IsSuccessStatusCode; }
            catch { return false; }
        }

        void EnsureServer(CancellationToken cancel)
        {
            if (ServerAlive()) return;
            StatusChanged?.Invoke("Starting the local AI models (about 20 s, first click only)...");
            if (serverProcess == null || serverProcess.HasExited)
            {
                var python = Path.Combine(Home, ".venv", "Scripts", "python.exe");
                if (!File.Exists(python)) throw new Exception("The OCR engine is not installed. Run install.bat again.");
                serverProcess = Process.Start(new ProcessStartInfo(python, "-m uvicorn app:app --host 127.0.0.1 --port 8765")
                {
                    WorkingDirectory = Path.Combine(Home, "server"),
                    CreateNoWindow = true,
                    UseShellExecute = false,
                });
                // the user's own work always comes first; the OCR only uses what is left
                try { serverProcess.PriorityClass = ProcessPriorityClass.BelowNormal; } catch { }
            }
            for (int i = 0; i < 180; i++)
            {
                cancel.ThrowIfCancellationRequested();
                if (ServerAlive()) return;
                Thread.Sleep(1000);
            }
            throw new Exception("The OCR engine did not start. Check " + Home);
        }

        static Dictionary<string, object> Ocr(byte[] image, CancellationToken cancel)
        {
            using (var form = new MultipartFormDataContent())
            {
                form.Add(new ByteArrayContent(image), "image", "image.png");
                var json = Http.PostAsync(Server + "/ocr", form, cancel).GetAwaiter().GetResult()
                    .EnsureSuccessStatusCode().Content.ReadAsStringAsync().GetAwaiter().GetResult();
                return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(json);
            }
        }

        // Word loads picture data lazily: a single InlineShape's Range.WordOpenXML often has no image
        // part for pictures not yet displayed, but the whole-document export always has every image.
        // Returns one entry per InlineShape (document order), or null if the order can't be matched.
        static byte[][] DocumentImages(string flatOpc, int inlineShapeCount)
        {
            var parts = XDocument.Parse(flatOpc).Root.Elements(Pkg + "part")
                .ToDictionary(p => (string)p.Attribute(Pkg + "name"));
            if (!parts.TryGetValue("/word/document.xml", out var main) ||
                !parts.TryGetValue("/word/_rels/document.xml.rels", out var rels)) return null;
            var targets = rels.Descendants(Rel + "Relationship")
                .ToDictionary(r => (string)r.Attribute("Id"), r => "/word/" + ((string)r.Attribute("Target")).TrimStart('/').Replace("word/", ""));
            var inlines = main.Descendants(WP + "inline")
                .Where(e => !e.Ancestors(W + "txbxContent").Any())  // text-box content is another story
                .ToList();
            if (inlines.Count != inlineShapeCount) return null;
            return inlines.Select(e =>
            {
                var id = (string)e.Descendants(A + "blip").FirstOrDefault()?.Attribute(R + "embed");
                if (id == null || !targets.TryGetValue(id, out var name) || !parts.TryGetValue(name, out var part)) return null;
                var data = part.Element(Pkg + "binaryData")?.Value;
                return data == null ? null : Convert.FromBase64String(data);
            }).ToArray();
        }

        static byte[] ImageBytes(string flatOpc)
        {
            // Every image goes to the server, which decides and logs why (unreadable formats included).
            var part = XDocument.Parse(flatOpc).Root.Elements(Pkg + "part")
                .FirstOrDefault(p => ((string)p.Attribute(Pkg + "contentType") ?? "").StartsWith("image/"));
            var data = part?.Element(Pkg + "binaryData")?.Value;
            return data == null ? null : Convert.FromBase64String(data);
        }

        static string FlatOpc(string omml) =>
            "<?xml version=\"1.0\" standalone=\"yes\"?>" +
            "<pkg:package xmlns:pkg=\"http://schemas.microsoft.com/office/2006/xmlPackage\">" +
            "<pkg:part pkg:name=\"/_rels/.rels\" pkg:contentType=\"application/vnd.openxmlformats-package.relationships+xml\"><pkg:xmlData>" +
            "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">" +
            "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"word/document.xml\"/>" +
            "</Relationships></pkg:xmlData></pkg:part>" +
            "<pkg:part pkg:name=\"/word/document.xml\" pkg:contentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"><pkg:xmlData>" +
            "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\" xmlns:m=\"http://schemas.openxmlformats.org/officeDocument/2006/math\">" +
            "<w:body><w:p>" + omml + "</w:p></w:body></w:document></pkg:xmlData></pkg:part></pkg:package>";
    }
}
