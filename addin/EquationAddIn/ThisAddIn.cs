using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using System.Xml.Linq;
using Word = Microsoft.Office.Interop.Word;

namespace EquationAddIn
{
    public partial class ThisAddIn
    {
        const string Server = "http://127.0.0.1:8765";
        static readonly XNamespace Pkg = "http://schemas.microsoft.com/office/2006/xmlPackage";
        static readonly HttpClient Http = new HttpClient { Timeout = TimeSpan.FromMinutes(2) };
        Process serverProcess;

        // Folder holding .venv and server\ : set by install.ps1, or EQUATION_ADDIN_HOME for development.
        static string Home =>
            Environment.GetEnvironmentVariable("EQUATION_ADDIN_HOME")
            ?? Microsoft.Win32.Registry.GetValue(@"HKEY_CURRENT_USER\Software\EquationAddIn", "Home", null) as string
            ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "equation-addin");

        protected override Microsoft.Office.Core.IRibbonExtensibility CreateRibbonExtensibilityObject() => new Ribbon();

        // The OCR server is started only when the button is clicked and exits by itself when idle,
        // so nothing heavy runs while Word is merely open.
        private void ThisAddIn_Startup(object sender, System.EventArgs e)
        {
        }

        private void ThisAddIn_Shutdown(object sender, System.EventArgs e)
        {
            try { if (serverProcess != null && !serverProcess.HasExited) serverProcess.Kill(); } catch { }
        }

        static bool ServerAlive()
        {
            try { return Http.GetAsync(Server + "/health").GetAwaiter().GetResult().IsSuccessStatusCode; }
            catch { return false; }
        }

        void StartServer()
        {
            var python = Path.Combine(Home, ".venv", "Scripts", "python.exe");
            if (!File.Exists(python)) return;
            serverProcess = Process.Start(new ProcessStartInfo(python, "-m uvicorn app:app --host 127.0.0.1 --port 8765")
            {
                WorkingDirectory = Path.Combine(Home, "server"),
                CreateNoWindow = true,
                UseShellExecute = false,
            });
        }

        void EnsureServer()
        {
            if (ServerAlive()) return;
            if (serverProcess == null || serverProcess.HasExited) StartServer();
            for (int i = 0; i < 120; i++)
            {
                Application.StatusBar = "Equation OCR: starting local server...";
                if (ServerAlive()) return;
                Thread.Sleep(1000);
            }
            throw new Exception("OCR server did not start. Check " + Home);
        }

        internal void ConvertActiveDocument()
        {
            EnsureServer();
            var doc = Application.ActiveDocument;
            var all = doc.InlineShapes.Cast<Word.InlineShape>().ToList();
            var images = DocumentImages(doc.WordOpenXML, all.Count);
            var shapes = Enumerable.Range(0, all.Count)
                .Where(k => all[k].Type == Word.WdInlineShapeType.wdInlineShapePicture
                         || all[k].Type == Word.WdInlineShapeType.wdInlineShapeLinkedPicture)
                .ToList();
            int converted = 0, skipped = 0, flagged = 0;
            Application.ScreenUpdating = false;
            try
            {
                // Last to first so earlier ranges stay valid after replacement.
                for (int i = shapes.Count - 1; i >= 0; i--)
                {
                    Application.StatusBar = $"Equation OCR: image {shapes.Count - i} of {shapes.Count}";
                    var range = all[shapes[i]].Range;
                    var image = images?[shapes[i]] ?? ImageBytes(range.WordOpenXML);
                    if (image == null) { skipped++; continue; }
                    var r = Ocr(image);
                    var status = (string)r["status"];
                    if (status == "converted")
                    {
                        range.InsertXML(FlatOpc((string)r["omml"]));
                        converted++;
                    }
                    else if (status == "flagged")
                    {
                        doc.Comments.Add(range, $"Equation OCR: please review ({r["reason"]}).\nLaTeX guess: {r["latex"]}");
                        flagged++;
                    }
                    else skipped++;
                }
            }
            finally
            {
                Application.ScreenUpdating = true;
                Application.StatusBar = "";
            }
            MessageBox.Show($"Converted: {converted}\nFlagged for review (commented): {flagged}\nSkipped (no readable image): {skipped}",
                "Equation OCR");
        }

        static readonly XNamespace W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
        static readonly XNamespace WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing";
        static readonly XNamespace A = "http://schemas.openxmlformats.org/drawingml/2006/main";
        static readonly XNamespace R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
        static readonly XNamespace Rel = "http://schemas.openxmlformats.org/package/2006/relationships";

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

        static Dictionary<string, object> Ocr(byte[] image)
        {
            using (var form = new MultipartFormDataContent())
            {
                form.Add(new ByteArrayContent(image), "image", "image.png");
                var json = Http.PostAsync(Server + "/ocr", form).GetAwaiter().GetResult()
                    .EnsureSuccessStatusCode().Content.ReadAsStringAsync().GetAwaiter().GetResult();
                return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(json);
            }
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

        #region VSTO generated code

        /// <summary>
        /// Required method for Designer support - do not modify
        /// the contents of this method with the code editor.
        /// </summary>
        private void InternalStartup()
        {
            this.Startup += new System.EventHandler(ThisAddIn_Startup);
            this.Shutdown += new System.EventHandler(ThisAddIn_Shutdown);
        }

        #endregion
    }
}
