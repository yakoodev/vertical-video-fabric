

def test_preprocess_timeout_scales_with_stream_length():
    from app.analysis_preprocess import preprocess_timeout

    assert preprocess_timeout(0) == 1200
    assert preprocess_timeout(600) == 1200
    assert preprocess_timeout(4.7 * 3600) >= 4.7 * 3600
