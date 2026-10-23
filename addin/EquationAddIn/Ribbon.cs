using System;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using Office = Microsoft.Office.Core;

namespace EquationAddIn
{
    [ComVisible(true)]
    public class Ribbon : Office.IRibbonExtensibility
    {
        public string GetCustomUI(string ribbonID)
        {
            using (var s = Assembly.GetExecutingAssembly().GetManifestResourceStream("EquationAddIn.Ribbon.xml"))
            using (var r = new StreamReader(s))
                return r.ReadToEnd();
        }

        public void OnConvertAll(Office.IRibbonControl control) => Run(() => Globals.ThisAddIn.Convert(selectionOnly: false));

        public void OnConvertSelection(Office.IRibbonControl control) => Run(() => Globals.ThisAddIn.Convert(selectionOnly: true));

        public void OnShowPane(Office.IRibbonControl control) => Run(() => Globals.ThisAddIn.ShowPane());

        static void Run(Action action)
        {
            try
            {
                action();
            }
            catch (Exception ex)
            {
                MessageBox.Show(ex.Message, "Equation OCR", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }
    }
}
