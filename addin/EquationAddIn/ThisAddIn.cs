using System.Collections.Generic;
using Microsoft.Office.Tools;
using Word = Microsoft.Office.Interop.Word;

namespace EquationAddIn
{
    public partial class ThisAddIn
    {
        // One side panel per Word window (Word requires that). The OCR server is started only when a
        // conversion begins and exits by itself when idle, so nothing heavy runs while Word is merely open.
        readonly Dictionary<Word.Window, CustomTaskPane> panes = new Dictionary<Word.Window, CustomTaskPane>();

        protected override Microsoft.Office.Core.IRibbonExtensibility CreateRibbonExtensibilityObject() => new Ribbon();

        internal EquationPane ShowPane()
        {
            var window = Application.ActiveWindow;
            if (!panes.TryGetValue(window, out var pane))
            {
                pane = CustomTaskPanes.Add(new EquationPane(), "Equation OCR", window);
                pane.Width = 330;
                panes[window] = pane;
            }
            pane.Visible = true;
            return (EquationPane)pane.Control;
        }

        internal void Convert(bool selectionOnly) => ShowPane().Start(selectionOnly);

        private void ThisAddIn_Startup(object sender, System.EventArgs e)
        {
        }

        private void ThisAddIn_Shutdown(object sender, System.EventArgs e)
        {
            Converter.Shutdown();
        }

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
