import React, { useEffect, useRef } from 'react';
import Plotly from 'plotly.js-basic-dist-min';

export default function SensorGraph({ sensorId, dates, tempData, watermarkData }) {
    const plotRef = useRef(null);

    useEffect(() => {
        if (!plotRef.current) return;
        if (!sensorId || !dates || dates.length === 0) {
            Plotly.purge(plotRef.current);
            return;
        }

        const traces = [
            {
                x: dates, y: tempData,
                type: 'scatter', mode: 'lines',
                name: 'Temperature (°C)',
                line:      { color: '#FF5722', width: 2, shape: 'spline', smoothing: 1.2 },
                fill:      'tozeroy',
                fillcolor: 'rgba(255,87,34,0.1)',
                yaxis: 'y1',
            },
            {
                x: dates, y: watermarkData,
                type: 'scatter', mode: 'lines',
                name: 'Watermark',
                line:      { color: '#03A9F4', width: 2, shape: 'spline', smoothing: 1.2 },
                fill:      'tozeroy',
                fillcolor: 'rgba(3,169,244,0.1)',
                yaxis: 'y2',
            },
        ];

        const layout = {
            autosize:      true,
            height:        450,
            margin:        { t: 20, b: 40, l: 50, r: 20 },
            showlegend:    true,
            legend:        { orientation: 'h', y: 1.15, x: 0.5, xanchor: 'center' },
            hovermode:     'x unified',
            plot_bgcolor:  'transparent',
            paper_bgcolor: 'transparent',
            xaxis:  { title: 'Timeline', showgrid: true, gridcolor: '#f5f5f5', zeroline: false },
            yaxis:  { title: 'Temp (°C)',  domain: [0.55, 1], showgrid: true, gridcolor: '#f5f5f5', zeroline: false },
            yaxis2: { title: 'Moisture',   domain: [0, 0.45], showgrid: true, gridcolor: '#f5f5f5', zeroline: false },
        };

        Plotly.react(plotRef.current, traces, layout, { responsive: true, displaylogo: false });
    }, [sensorId, dates, tempData, watermarkData]);

    useEffect(() => {
        if (!plotRef.current) return;
        const ro = new ResizeObserver(() => Plotly.Plots.resize(plotRef.current));
        ro.observe(plotRef.current);
        return () => ro.disconnect();
    }, []);

    return (
        <div style={{ width: '100%', backgroundColor: '#ffffff', padding: '20px', borderRadius: '12px', boxShadow: '0 8px 24px rgba(0,0,0,0.1)' }}>
            <h3 style={{ marginTop: 0, color: '#1a3a2a', borderBottom: '2px solid #eaeaea', paddingBottom: '10px', fontSize: '16px' }}>
                {sensorId ? `NODE TELEMETRY: ${sensorId.toUpperCase()}` : 'SELECT A SENSOR NODE'}
            </h3>
            {sensorId && dates && dates.length > 0
                ? <div ref={plotRef} style={{ width: '100%', height: '450px' }} />
                : <div style={{ height: '400px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#8a91a4', fontStyle: 'italic' }}>
                    Click a node on the map to load historical telemetry.
                </div>
            }
        </div>
    );
}
