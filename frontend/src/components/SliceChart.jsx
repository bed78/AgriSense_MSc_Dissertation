import React, { useState, useEffect, useRef } from 'react';
import Plotly from 'plotly.js-basic-dist-min';

export default function SliceChart({ sensorIds, dateFrom, dateTo }) {
    const [data,    setData]    = useState(null);
    const [loading, setLoading] = useState(false);
    const plotRef = useRef(null);

    useEffect(() => {
        if (sensorIds.length < 2) return;
        const fetchSlice = async () => {
            setLoading(true);
            try {
                const res = await fetch(
                    `http://localhost:8000/slice/compute?date_from=${dateFrom}&date_to=${dateTo}`,
                    {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ sensor_ids: sensorIds }),
                    }
                );
                if (res.ok) setData(await res.json());
                else setData(null);
            } catch (e) {
                setData(null);
            } finally {
                setLoading(false);
            }
        };
        fetchSlice();
    }, [sensorIds, dateFrom, dateTo]);

    useEffect(() => {
        if (!plotRef.current || !data) return;

        const traces = [
            {
                x: data.distance_axis, y: data.temperature,
                type: 'scatter', mode: 'lines',
                name: 'Temp (°C)',
                line: { color: '#E8593C', width: 3 },
            },
            {
                x: data.distance_axis, y: data.watermark,
                type: 'scatter', mode: 'lines',
                name: 'Watermark (cb)',
                yaxis: 'y2',
                line: { color: '#378ADD', width: 3 },
            },
        ];

        const layout = {
            paper_bgcolor: 'transparent',
            plot_bgcolor:  'transparent',
            font:          { color: '#d1d4dc' },
            autosize:      true,
            margin:        { l: 50, r: 50, b: 50, t: 20 },
            xaxis:  {
                title:     'Distance across cross-section (meters)',
                gridcolor: '#2a2e39',
                tickfont:  { color: '#8a91a4' },
                titlefont: { color: '#8a91a4' },
            },
            yaxis:  {
                title:     'Temperature (°C)',
                gridcolor: '#2a2e39',
                titlefont: { color: '#E8593C' },
                tickfont:  { color: '#E8593C' },
            },
            yaxis2: {
                title:      'Watermark (cb)',
                titlefont:  { color: '#378ADD' },
                tickfont:   { color: '#378ADD' },
                overlaying: 'y',
                side:       'right',
            },
        };

        Plotly.react(plotRef.current, traces, layout, { responsive: true, displaylogo: false });
    }, [data]);

    useEffect(() => {
        if (!plotRef.current) return;
        const ro = new ResizeObserver(() => Plotly.Plots.resize(plotRef.current));
        ro.observe(plotRef.current);
        return () => ro.disconnect();
    }, []);

    if (sensorIds.length < 2)
        return <div style={{ padding: '20px', color: '#8a91a4' }}>Select at least 2 sensors on the map to compute a slice.</div>;
    if (loading)
        return <div style={{ padding: '20px', color: '#8a91a4' }}>Computing Delaunay interpolation...</div>;
    if (!data)
        return <div style={{ padding: '20px', color: '#8a91a4' }}>Failed to compute slice.</div>;

    return <div ref={plotRef} style={{ width: '100%', height: '100%' }} />;
}
