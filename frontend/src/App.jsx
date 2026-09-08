/**
 * App — top-level component and page router for the AgriSense frontend.
 *
 * Owns all shared state (active tab, selected date range, selected
 * sensors/mode, report target) and renders one of four "pages" based on
 * `currentTab`: home, health, map, visualization, and reports. There's no
 * routing library — page switching is just conditional rendering of a
 * single-page app.
 */
import React, { useState, useEffect, Suspense, lazy } from 'react';
// Lazy-loaded: these three pull in Leaflet (~1.1MB) and Plotly (~20MB+
// unminified) respectively. Importing them eagerly meant every tab —
// even Home, which never touches a map or chart — paid for downloading
// and parsing both libraries before it could render. Deferring the
// import until the Map/Visualization tab actually mounts keeps Home,
// Sensor Health, and Reports fast.
const SensorMap = lazy(() => import('./components/SensorMap'));
const SensorChart = lazy(() => import('./components/SensorChart'));
const SliceChart = lazy(() => import('./components/SliceChart'));

export default function App() {
  const [currentTab, setCurrentTab] = useState('home');
  const [networkHealth, setNetworkHealth] = useState([]); // per-sensor online/offline status, for the Health page
  const [sensors, setSensors] = useState([]); // master list of sensors + coordinates
  const [isInitialLoading, setIsInitialLoading] = useState(true); // true until the startup /sensors + /network/health fetches settle
  const [isMapLoading, setIsMapLoading] = useState(false); // mirrors SensorMap's own isLoading, via onLoadingChange

  const [dateFrom, setDateFrom] = useState('2022-01-01');
  const [dateTo, setDateTo] = useState(new Date().toISOString().split('T')[0]); // defaults to today
  const [mode, setMode] = useState('sensor'); // 'sensor' = profile view, 'slice' = cross-section view
  const [selectedSensors, setSelectedSensors] = useState([]); // sensor IDs (or "lat,lng" waypoints in slice mode)

  // Snapshot of `mode`/`selectedSensors` taken when "Generate Report" is
  // clicked, so the Reports page always reflects exactly what was
  // visualized rather than tracking live map-selection changes.
  const [reportMode, setReportMode] = useState('sensor');
  const [reportTarget, setReportTarget] = useState('FF-01');

  // Sorts sensor/health records numerically by the digits in their ID
  // (e.g. "FF-2" before "FF-10"), since a plain string sort would put
  // "FF-10" before "FF-2".
  const sortById = (arr, idKey = 'id') =>
    [...arr].sort((a, b) => {
      const numA = parseInt(String(a[idKey] || '').match(/\d+/)?.[0] ?? '0', 10);
      const numB = parseInt(String(b[idKey] || '').match(/\d+/)?.[0] ?? '0', 10);
      return numA - numB;
    });

  // Load the master sensor list and current network health once on mount.
  useEffect(() => {
    setIsInitialLoading(true);
    Promise.all([
      fetch('http://localhost:8000/sensors').then(res => res.json()).then(data => setSensors(sortById(data, 'sensor_id'))),
      fetch('http://localhost:8000/network/health').then(res => res.json()).then(data => setNetworkHealth(sortById(data, 'id'))),
    ]).finally(() => setIsInitialLoading(false));
  }, []);

  /**
   * Handles a click coming from the map (SensorMap's onSensorClick).
   * `sid` is either a raw "lat,lng" string (an arbitrary map click, used
   * for slice waypoints) or a sensor ID string (a click on a sensor
   * marker). Both cases toggle membership in `selectedSensors`.
   */
  const handleMapClick = (sid) => {
      // Coordinate string ("lat,lng") — used for arbitrary slice waypoints and
      // sensor clicks in slice mode. Use proximity matching for deselection
      // because floating-point rounding means two clicks on the same spot may
      // not produce bit-identical strings.
      if (typeof sid === 'string' && sid.includes(',') && !sid.startsWith('ff') && !sid.startsWith('FF')) {
          const [inLat, inLng] = sid.split(',').map(Number);
          if (!isNaN(inLat) && !isNaN(inLng)) {
              setSelectedSensors(prev => {
                  // Find an existing waypoint within ~1 metre of this click
                  const nearIdx = prev.findIndex(item => {
                      if (!item.includes(',')) return false;
                      const [eLat, eLng] = item.split(',').map(Number);
                      return Math.abs(eLat - inLat) < 0.0001 && Math.abs(eLng - inLng) < 0.0001;
                  });
                  // Toggle: remove if found, append if new
                  return nearIdx >= 0
                      ? prev.filter((_, i) => i !== nearIdx)
                      : [...prev, sid];
              });
              return;
          }
      }
      // Sensor ID — exact string toggle (unchanged behaviour)
      setSelectedSensors(prev => prev.includes(sid) ? prev.filter(id => id !== sid) : [...prev, sid]);
  };

  // NEW: Handlers for the Select All functionality
  // Selects every sensor that has valid coordinates (skips any with
  // missing lat/lon, which can't be plotted or interpolated anyway).
  const handleSelectAll = () => {
      const validIds = sensors.filter(s => s.lat && s.lon).map(s => s.sensor_id);
      setSelectedSensors(validIds);
  };

  // Validates the current selection before switching to the
  // Visualization page: needs at least 1 sensor (sensor mode) or at
  // least 2 (slice mode, since a cross-section needs two endpoints).
  const handleVisualizeClick = () => {
      if (selectedSensors.length === 0) return alert("Please select a sensor on the map first.");
      if (mode === 'slice' && selectedSensors.length < 2) return alert("Please select at least 2 sensors for a slice.");
      setCurrentTab('visualization');
  };

  // Snapshots the current mode + full sensor selection into
  // reportMode/reportTarget and jumps to the Reports page.
  const handleGenerateReportClick = () => {
      setReportMode(mode);
      // Always carry the FULL selection through — dropping to selectedSensors[0]
      // here (the old sensor-mode behaviour) meant a 4-node Sensor Profile
      // comparison would silently collapse into a single-sensor report.
      setReportTarget(selectedSensors.join(','));
      setCurrentTab('reports');
  };

  /**
   * Triggers a browser download for both the CSV and PDF export of the
   * current report target, by momentarily creating and clicking hidden
   * <a download> links. Picks the correct backend endpoint (single
   * sensor / multi-sensor batch / slice) based on reportMode and how
   * many IDs are in reportTarget.
   */
  const downloadBoth = () => {
    if (!reportTarget) return;
    const targetIds = reportTarget.split(',').filter(Boolean);
    const isSingleSensor = reportMode === 'sensor' && targetIds.length === 1;
    const isBatchSensor  = reportMode === 'sensor' && targetIds.length > 1;

    // Three possible export shapes: one sensor, several sensors
    // (comparative batch), or a slice (cross-section) path.
    const endpointCsv = isSingleSensor
        ? `http://localhost:8000/export/csv?sensor_id=${targetIds[0]}&date_from=${dateFrom}&date_to=${dateTo}`
        : isBatchSensor
        ? `http://localhost:8000/export/batch/csv?sensor_ids=${reportTarget}&date_from=${dateFrom}&date_to=${dateTo}`
        : `http://localhost:8000/export/slice/csv?sensor_ids=${reportTarget}&date_from=${dateFrom}&date_to=${dateTo}`;

    const endpointPdf = isSingleSensor
        ? `http://localhost:8000/export/pdf?sensor_id=${targetIds[0]}&date_from=${dateFrom}&date_to=${dateTo}`
        : isBatchSensor
        ? `http://localhost:8000/export/batch/pdf?sensor_ids=${reportTarget}&date_from=${dateFrom}&date_to=${dateTo}`
        : `http://localhost:8000/export/slice/pdf?sensor_ids=${reportTarget}&date_from=${dateFrom}&date_to=${dateTo}`;

    // Create a throwaway <a download> element, click it programmatically,
    // then remove it — this is the standard trick for triggering a file
    // download from JS without navigating the page away.
    const linkCsv = document.createElement('a');
    linkCsv.href = endpointCsv;
    linkCsv.setAttribute('download', '');
    document.body.appendChild(linkCsv);
    linkCsv.click();
    document.body.removeChild(linkCsv);

    const linkPdf = document.createElement('a');
    linkPdf.href = endpointPdf;
    linkPdf.setAttribute('download', '');
    linkPdf.setAttribute('target', '_blank'); 
    document.body.appendChild(linkPdf);
    linkPdf.click();
    document.body.removeChild(linkPdf);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', backgroundColor: '#f4f6f8', fontFamily: 'sans-serif', color: '#333', cursor: (isInitialLoading || isMapLoading) ? 'wait' : 'default' }}>
      
      {/* NAVIGATION BAR — tab buttons drive which "page" section renders below.
          Note 'visualization' isn't in this list; it's only reachable via
          the "Render Visualization" button on the map page. */}
      <div style={{ display: 'flex', alignItems: 'center', padding: '15px 30px', backgroundColor: 'white', borderBottom: '1px solid #e0e0e0', gap: '30px', zIndex: 50 }}>
         <strong style={{ fontSize: '20px', color: '#1a472a', marginRight: '20px' }}>AgriSensors Platform</strong>
         {['home', 'health', 'map', 'reports'].map(tab => (
             <button key={tab} onClick={() => setCurrentTab(tab)} style={{ background: 'none', border: 'none', fontSize: '15px', fontWeight: currentTab === tab ? 'bold' : 'normal', color: currentTab === tab ? '#1a472a' : '#666', cursor: 'pointer', textTransform: 'capitalize' }}>
                 {tab === 'health' ? 'Sensor Health' : tab}
             </button>
         ))}
      </div>

      {/* MAIN CONTENT WRAPPER */}
      <main style={{ flex: 1, overflowY: 'auto', padding: currentTab === 'map' || currentTab === 'visualization' || currentTab === 'home' ? '0' : '30px' }}>

        {/* --- HOMEPAGE --- Landing page with three clickable cards that
            jump straight to the Map, Health, or Reports tabs. */}
        {currentTab === 'home' && (
            <div style={{ backgroundColor: '#dbcebc', minHeight: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', padding: '40px 20px', boxSizing: 'border-box' }}>
                <h1 style={{ color: '#193026', fontFamily: 'serif', fontSize: '46px', margin: '0 0 15px 0', textAlign: 'center' }}>
                Tektelic Kiwi AgriSensors Network
                </h1>
                <p style={{ color: '#5c5446', fontSize: '16px', marginBottom: '60px', textAlign: 'center', maxWidth: '600px', lineHeight: '1.6' }}>
                    Select a module below to analyze spatiotemporal topologies, track hardware telemetry, or generate formal data reports.
                </p>

                <div style={{ display: 'flex', justifyContent: 'center', gap: '30px', flexWrap: 'nowrap', maxWidth: '1200px', width: '100%' }}>
                    
                    {/* Card 1: Map */}
                    <div 
                        onClick={() => setCurrentTab('map')}
                        style={{ backgroundColor: '#193026', width: '280px', padding: '40px 30px', display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center', cursor: 'pointer', boxShadow: '0 15px 30px rgba(0,0,0,0.1)', transition: 'transform 0.2s', borderRadius: '4px' }}
                        onMouseEnter={(e) => e.currentTarget.style.transform = 'translateY(-5px)'}
                        onMouseLeave={(e) => e.currentTarget.style.transform = 'translateY(0)'}
                    >
                        <div style={{ width: '45px', height: '45px', border: '1px solid #eaddc4', borderRadius: '8px', display: 'flex', justifyContent: 'center', alignItems: 'center', marginBottom: '25px' }}>
                            <span style={{ fontSize: '20px' }}>🗺️</span>
                        </div>
                        <h2 style={{ color: '#eaddc4', fontSize: '18px', margin: '0 0 15px 0', fontFamily: 'serif', fontWeight: 'normal' }}>Interactive Map</h2>
                        <p style={{ color: '#b0a68f', fontSize: '13px', lineHeight: '1.6', margin: 0 }}>Access live sensor profiles and compute Delaunay-interpolated cross-sections across the network.</p>
                    </div>

                    {/* Card 2: Health */}
                    <div 
                        onClick={() => setCurrentTab('health')}
                        style={{ backgroundColor: '#193026', width: '280px', padding: '40px 30px', display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center', cursor: 'pointer', boxShadow: '0 15px 30px rgba(0,0,0,0.1)', transition: 'transform 0.2s', borderRadius: '4px' }}
                        onMouseEnter={(e) => e.currentTarget.style.transform = 'translateY(-5px)'}
                        onMouseLeave={(e) => e.currentTarget.style.transform = 'translateY(0)'}
                    >
                        <div style={{ width: '45px', height: '45px', border: '1px solid #eaddc4', borderRadius: '8px', display: 'flex', justifyContent: 'center', alignItems: 'center', marginBottom: '25px' }}>
                            <span style={{ fontSize: '20px' }}>🔋</span>
                        </div>
                        <h2 style={{ color: '#eaddc4', fontSize: '18px', margin: '0 0 15px 0', fontFamily: 'serif', fontWeight: 'normal' }}>Sensor Health</h2>
                        <p style={{ color: '#b0a68f', fontSize: '13px', lineHeight: '1.6', margin: 0 }}>Monitor the uptime, heartbeat status, and geospatial coordinate configuration of all existingnodes.</p>
                    </div>

                    {/* Card 3: Reports */}
                    <div 
                        onClick={() => setCurrentTab('reports')}
                        style={{ backgroundColor: '#193026', width: '280px', padding: '40px 30px', display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center', cursor: 'pointer', boxShadow: '0 15px 30px rgba(0,0,0,0.1)', transition: 'transform 0.2s', borderRadius: '4px' }}
                        onMouseEnter={(e) => e.currentTarget.style.transform = 'translateY(-5px)'}
                        onMouseLeave={(e) => e.currentTarget.style.transform = 'translateY(0)'}
                    >
                        <div style={{ width: '45px', height: '45px', border: '1px solid #eaddc4', borderRadius: '8px', display: 'flex', justifyContent: 'center', alignItems: 'center', marginBottom: '25px' }}>
                            <span style={{ fontSize: '20px' }}>📄</span>
                        </div>
                        <h2 style={{ color: '#eaddc4', fontSize: '18px', margin: '0 0 15px 0', fontFamily: 'serif', fontWeight: 'normal' }}>Reports Center</h2>
                        <p style={{ color: '#b0a68f', fontSize: '13px', lineHeight: '1.6', margin: 0 }}>Generate and download formal PDF summaries and raw CSV datasets for external analysis.</p>
                    </div>

                </div>
            </div>
        )}

        {/* --- HEALTH PAGE --- Summary stats (total/active/inactive nodes)
            followed by a card grid, one card per sensor, showing its
            online status, coordinates, and last heartbeat time. */}
        {currentTab === 'health' && (
          <div style={{ maxWidth: '1400px', margin: '0 auto' }}>
              <div style={{ backgroundColor: '#1a3a2a', color: 'white', padding: '30px', borderRadius: '12px', marginBottom: '30px' }}>
                  <h2 style={{ margin: '0 0 10px 0' }}>Sensor Monitoring Hub</h2>
                  <div style={{ display: 'flex', gap: '40px', marginTop: '20px' }}>
                      <div><div style={{ fontSize: '14px', color: '#a0bfa0' }}>TOTAL NODES</div><div style={{ fontSize: '36px', fontWeight: 'bold' }}>{networkHealth.length}</div></div>
                      <div><div style={{ fontSize: '14px', color: '#00e676' }}>ACTIVE</div><div style={{ fontSize: '36px', fontWeight: 'bold', color: '#00e676' }}>{networkHealth.filter(n => n.status === 'Online').length}</div></div>
                      <div><div style={{ fontSize: '14px', color: '#ff5252' }}>INACTIVE</div><div style={{ fontSize: '36px', fontWeight: 'bold', color: '#ff5252' }}>{networkHealth.filter(n => n.status === 'Offline').length}</div></div>
                  </div>
              </div>

              <h3 style={{ borderBottom: '2px solid #ddd', paddingBottom: '10px' }}>Sensor Node Status</h3>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: '20px' }}>
                  {networkHealth.map(node => (
                      <div key={node.id} style={{ backgroundColor: 'white', border: '1px solid #e0e0e0', borderRadius: '10px', padding: '20px', boxShadow: '0 2px 4px rgba(0,0,0,0.02)' }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px' }}>
                              <strong style={{ fontSize: '20px', color: '#333' }}>{node.id}</strong>
                              <span style={{ fontSize: '14px', fontWeight: 'bold', color: node.status === 'Online' ? '#00e676' : '#ff5252', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                  <div style={{ width: '10px', height: '10px', borderRadius: '50%', backgroundColor: node.status === 'Online' ? '#00e676' : '#ff5252' }}></div>
                                  {node.status}
                              </span>
                          </div>
                          <div style={{ backgroundColor: '#f8f9fa', padding: '15px', borderRadius: '6px', marginBottom: '15px', border: '1px solid #eee' }}>
                              <div style={{ fontSize: '12px', color: '#888', fontWeight: 'bold', marginBottom: '5px' }}>GEOSPATIAL COORDINATES</div>
                              <div style={{ fontSize: '16px', color: '#1a472a', fontWeight: 'bold', fontFamily: 'monospace' }}>Lat: {node.lat}</div>
                              <div style={{ fontSize: '16px', color: '#1a472a', fontWeight: 'bold', fontFamily: 'monospace' }}>Lon: {node.lon}</div>
                          </div>
                          <div style={{ fontSize: '12px', color: '#888' }}>Last heartbeat: <strong>{node.last_seen}</strong></div>
                      </div>
                  ))}
              </div>
          </div>
        )}

        {/* --- MAP PAGE --- Toolbar (mode toggle, date range, visualize button)
            above the interactive SensorMap. Switching mode clears the current
            selection since sensor-profile picks and slice waypoints aren't
            interchangeable. */}
        {currentTab === 'map' && (
            <div style={{ display: 'flex', flexDirection: 'column', height: '100%', width: '100%' }}>
                <div style={{ padding: '15px 30px', backgroundColor: 'white', borderBottom: '1px solid #e0e0e0', display: 'flex', gap: '30px', alignItems: 'center', zIndex: 10 }}>
                    <div style={{ display: 'flex', gap: '10px' }}>
                        <button style={{ background: mode === 'sensor' ? '#1a3a2a' : '#f0f2f5', color: mode === 'sensor' ? 'white' : '#555', border: 'none', padding: '10px 15px', borderRadius: '6px', cursor: 'pointer', fontWeight: 'bold' }} onClick={() => { setMode('sensor'); setSelectedSensors([]); }}>Sensor Profile</button>
                        <button style={{ background: mode === 'slice' ? '#1a3a2a' : '#f0f2f5', color: mode === 'slice' ? 'white' : '#555', border: 'none', padding: '10px 15px', borderRadius: '6px', cursor: 'pointer', fontWeight: 'bold' }} onClick={() => { setMode('slice'); setSelectedSensors([]); }}>Slice</button>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <span style={{ fontSize: '14px', fontWeight: 'bold', color: '#888' }}>DATE:</span>
                        <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} style={{ padding: '8px', border: '1px solid #ccc', borderRadius: '4px' }}/>
                        <span>to</span>
                        <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} style={{ padding: '8px', border: '1px solid #ccc', borderRadius: '4px' }}/>
                    </div>

                    <button onClick={handleVisualizeClick} style={{ marginLeft: 'auto', backgroundColor: '#00e676', color: '#1a3a2a', padding: '10px 25px', border: 'none', borderRadius: '6px', fontWeight: 'bold', cursor: 'pointer' }}>Render Visualization →</button>
                </div>

                <div style={{ flex: 1, position: 'relative', minHeight: '500px', display: 'flex' }}>
                    <Suspense fallback={<div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#888' }}>Loading map…</div>}>
                        <SensorMap
                            mode={mode}
                            dateFrom={dateFrom}
                            dateTo={dateTo}
                            activeSensors={selectedSensors}
                            onSensorClick={handleMapClick}
                            onSelectAll={(ids) => setSelectedSensors(ids)}
                            onClearAll={() => setSelectedSensors([])}
                            onLoadingChange={setIsMapLoading}
                        />
                    </Suspense>
                </div>
            </div>
        )}

        {/* --- VISUALIZATION PAGE --- Renders SensorChart (sensor mode) or
            SliceChart (slice mode) for the sensors selected on the map page. */}
        {currentTab === 'visualization' && (
             <div style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column', padding: '20px', boxSizing: 'border-box' }}>
                 <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px' }}>
                    
                     {/* Replace the h2 heading */}
                    <h2 style={{ margin: 0 }}>{mode === 'sensor' ? `Sensor Profile (${selectedSensors.length} Nodes)` : `Cross-Section (${selectedSensors.length} Nodes)`}</h2>

                     {selectedSensors.length > 0 && (
                         <button onClick={handleGenerateReportClick} style={{ backgroundColor: '#2e5b3e', color: 'white', padding: '10px 20px', border: 'none', borderRadius: '6px', fontWeight: 'bold', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '8px' }}>
                             📄 Generate Formal Report
                         </button>
                     )}
                 </div>
                 
                 <div style={{ flex: 1, backgroundColor: '#131722', borderRadius: '12px', padding: '20px', boxShadow: '0 4px 12px rgba(0,0,0,0.1)', overflow: 'hidden' }}>
                      <Suspense fallback={<div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#888' }}>Loading chart…</div>}>
                          {mode === 'sensor' ? <SensorChart sensorIds={selectedSensors} dateFrom={dateFrom} dateTo={dateTo} /> : <SliceChart sensorIds={selectedSensors} dateFrom={dateFrom} dateTo={dateTo} />}
                      </Suspense>
                 </div>
             </div>
        )}

        {/* --- REPORTS PAGE --- Left sidebar shows the (read-only) report
            target and date range plus a download button; the right panel
            embeds a live PDF preview via an <iframe> pointed at the export
            endpoint with preview=true. */}
        {currentTab === 'reports' && (
            <div style={{ maxWidth: '1200px', margin: '0 auto', height: '100%' }}>
                <h2 style={{ marginBottom: '20px' }}>Reports & Export Center</h2>
                <div style={{ display: 'flex', gap: '30px', height: 'calc(100% - 60px)' }}>
                    <div style={{ width: '300px', backgroundColor: 'white', padding: '25px', borderRadius: '12px', border: '1px solid #e0e0e0', display: 'flex', flexDirection: 'column', gap: '20px' }}>
                        <div>
                            <label style={{ fontSize: '13px', fontWeight: 'bold', color: '#888', display: 'block', marginBottom: '8px' }}>
                                {reportMode === 'sensor' ? 'TARGET SENSOR(S)' : 'CROSS-SECTION PATH'}
                            </label>

                            {/* Read-only — always mirrors the exact sensor selection used to
                                render the on-screen visualization, so the exported report can't
                                drift from what was actually plotted. */}
                            <div style={{ padding: '12px', borderRadius: '6px', border: '1px solid #ccc', backgroundColor: '#f9f9f9', wordWrap: 'break-word', fontWeight: 'bold', color: '#1a3a2a' }}>
                                {reportTarget ? reportTarget.split(',').join(reportMode === 'sensor' ? ', ' : ' ➔ ').toUpperCase() : '—'}
                            </div>
                        </div>

                        <div>
                            <label style={{ fontSize: '13px', fontWeight: 'bold', color: '#888', display: 'block', marginBottom: '8px' }}>DATE RANGE</label>
                            <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} style={{ width: '100%', padding: '12px', borderRadius: '6px', border: '1px solid #ccc', marginBottom: '10px', boxSizing: 'border-box' }} />
                            <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} style={{ width: '100%', padding: '12px', borderRadius: '6px', border: '1px solid #ccc', boxSizing: 'border-box' }} />
                        </div>
                        <button onClick={downloadBoth} style={{ backgroundColor: '#1a3a2a', color: 'white', padding: '16px', border: 'none', borderRadius: '8px', fontSize: '16px', fontWeight: 'bold', cursor: 'pointer', marginTop: 'auto' }}>
                            Download PDF & CSV
                        </button>
                    </div>

                    <div style={{ flex: 1, backgroundColor: '#e9ecef', borderRadius: '12px', border: '1px solid #e0e0e0', padding: '20px', display: 'flex', flexDirection: 'column' }}>
                        <div style={{ flex: 1, backgroundColor: 'white', borderRadius: '8px', overflow: 'hidden', boxShadow: '0 4px 12px rgba(0,0,0,0.1)' }}>
                            {/* Same single/batch/slice endpoint selection as
                                downloadBoth() above, but pointed at the PDF
                                export with preview=true so it renders inline
                                in the iframe instead of downloading. */}
                            <iframe 
                                src={(() => {
                                    const targetIds = reportTarget.split(',').filter(Boolean);
                                    if (reportMode === 'sensor' && targetIds.length === 1) {
                                        return `http://localhost:8000/export/pdf?sensor_id=${targetIds[0]}&date_from=${dateFrom}&date_to=${dateTo}&preview=true`;
                                    }
                                    if (reportMode === 'sensor') {
                                        return `http://localhost:8000/export/batch/pdf?sensor_ids=${reportTarget}&date_from=${dateFrom}&date_to=${dateTo}&preview=true`;
                                    }
                                    return `http://localhost:8000/export/slice/pdf?sensor_ids=${reportTarget}&date_from=${dateFrom}&date_to=${dateTo}&preview=true`;
                                })()} 
                                style={{ width: '100%', height: '100%', border: 'none' }} title="PDF Preview"/>
                        </div>
                    </div>
                </div>
            </div>
        )}

      </main>
    </div>
  );
}