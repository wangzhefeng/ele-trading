

def flat_valley_price_diff(df_input):
    '''
    拉平深谷与谷的电价差，防止充电功率在乘上月数的倍率后倾向深谷时段, 上海电价特供（2025年）
    '''
    if df_input["eleType"].isin(["谷"]).any():
        v_index = df_input[df_input["eleType"] == "谷"].index[-1]
    else:
        v_index = None
    
    if df_input["eleType"].isin(["深谷"]).any():
        dv_index = df_input[df_input["eleType"] == "深谷"].index[-1]
    else:
        dv_index = None
    
    if v_index and dv_index:
        flat_price_index = max(v_index, dv_index)
        flat_price = df_input.loc[flat_price_index, "elePrice"]
    elif v_index and not dv_index:
        flat_price_index = v_index
        flat_price = df_input.loc[flat_price_index, "elePrice"]
    elif not v_index and dv_index:
        flat_price_index = dv_index
        flat_price = df_input.loc[flat_price_index, "elePrice"]
    else:
        return df_input
    
    df_input.loc[df_input['eleType'] == '谷', 'elePrice'] = flat_price
    df_input.loc[df_input['eleType'] == '深谷', 'elePrice'] = flat_price
    
    return df_input
