// GitHub-backed replacement for the direct NFL Mock Draft Database fetch.
// Keep your old code if you want; change refreshUSTS() to call:
//   refreshDraftProjectionsGitHub();
// instead of refreshDraftProjections().

function refreshDraftProjectionsGitHub() {
  const ss = SpreadsheetApp.getActive();
  const target = ss.getSheetByName('Draft_Projection');
  if (!target) throw new Error('Missing Draft_Projection sheet.');

  const csvUrl = 'https://raw.githubusercontent.com/oxiwun/usts-draft-projections/main/data/draft_projection.csv';

  const oldRows = target.getLastRow() > 1
    ? target.getRange(2, 1, target.getLastRow() - 1, 10).getValues()
    : [];

  try {
    const res = UrlFetchApp.fetch(csvUrl, {
      method: 'get',
      muteHttpExceptions: true,
      followRedirects: true,
      headers: {'User-Agent': 'uSTS-draft-projection-importer'}
    });
    const code = res.getResponseCode();
    if (code < 200 || code >= 300) throw new Error('GitHub CSV HTTP ' + code);

    const parsed = Utilities.parseCsv(res.getContentText());
    if (parsed.length < 2) throw new Error('GitHub CSV contains no player rows.');

    const expected = [
      'Draft_Year','Projected_Pick','Player','Position','College',
      'Name_Key','Match_Key','Source_URL','Last_Updated','Status'
    ];
    const header = parsed[0].map(String);
    if (expected.some((h, i) => header[i] !== h)) {
      throw new Error('Unexpected CSV header.');
    }

    const byYear = {};
    parsed.slice(1).forEach(r => {
      const year = Number(r[0]);
      const pick = Number(r[1]);
      if (!year || !pick || pick < 1 || pick > 256) return;
      (byYear[year] || (byYear[year] = [])).push(r.slice(0,10));
    });

    const rows = [];
    Object.keys(byYear).sort().forEach(y => {
      byYear[y]
        .sort((a,b) => Number(a[1]) - Number(b[1]))
        .slice(0,256)
        .forEach(r => rows.push(r));
    });
    if (!rows.length) throw new Error('No valid ranks 1-256 found in CSV.');

    if (target.getLastRow() > 1) {
      target.getRange(2,1,target.getLastRow()-1,10).clearContent();
    }
    target.getRange(2,1,rows.length,10).setValues(rows);

    const status = `${rows.length} rows imported from GitHub at ` +
      Utilities.formatDate(new Date(), 'America/New_York', 'yyyy-MM-dd HH:mm');
    const mainSetup = ss.getSheetByName('Setup');
    if (mainSetup) mainSetup.getRange('A40:B40').setValues([['MDDB projection refresh', status]]);
  } catch (err) {
    // Never wipe a last-good projection snapshot because a daily import failed.
    if (!oldRows.length) {
      throw err;
    }
    const mainSetup = ss.getSheetByName('Setup');
    if (mainSetup) mainSetup.getRange('A40:B40').setValues([[
      'MDDB projection refresh',
      'GitHub import failed; prior snapshot preserved (' + err.message + ')'
    ]]);
  }
}