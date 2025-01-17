import geopandas as gpd
import pandas as pd
import sys
import ruamel.yaml
from geopy import distance
yaml = ruamel.yaml.YAML()
from . import parameters as pr
import os

vre_dic = {'Wind':['onshore_wind',5],'Solar':['rooftop_pv',170],'Wind Offshore':['offshore_wind',5.36]}

dc_links = pd.read_csv('data/links/dc_links.csv')

def export_timeseries(regions_geo, ds_regions,data_name,sign):
    df = pd.DataFrame(index= ds_regions.time.values)
    for i,rows in regions_geo.iterrows():
        if len(ds_regions[data_name].loc[rows.nuts_2s].values) != 0:
            df[rows.id] = ds_regions[data_name].loc[rows.nuts_2s].values
    df = df * sign
    df.to_csv('calliope_model/timeseries_data/{}.csv'.format(data_name))

def create_timeseries_csv(regions_geo, ds_regions, sectors):
    data_list = [{'power':-1}, {'heat':-1}, {'pv_cf':1}, {'wind_cf':1},
                    {'wind_offshore_cf':1}, {'hydro_inflow':1},
                    {'cop_air':1}]
    if 'iron and steel' in sectors:
        data_list.append({'hydrogen':-1})
        ds_regions['power'] = ds_regions['power'] + ds_regions['industries_demand'].loc[{'e_form':'power','sector':'Iron and steel'}]
        ds_regions['hydrogen'] = ds_regions['industries_demand'].loc[{'e_form':'hydrogen','sector':'Iron and steel'}]
    for series in data_list:
        v, k = series.popitem()
        export_timeseries(regions_geo, ds_regions,v,k)

def create_location_yaml(regions_geo, ds_regions, sectors):
    ds_regions["power_plants"] = ds_regions["power_plants"].groupby('tech').sum('fuel')
    yaml = ruamel.yaml.YAML()

    if len(regions_geo) > 1 :
        dict_file = {'locations': {},'links': {}}
    else:
        dict_file = {'locations': {}}

    line_lenght = [0]
    for i, rows in regions_geo.iterrows():
        dict_file['locations'][rows.id]= {}
        coords = rows.geometry.centroid
        dict_file['locations'][rows.id]['coordinates'] = {'lat':round(coords.y,2),'lon':round(coords.x,2)}
        dict_file['locations'][rows.id]['techs'] = {}
        dict_file['locations'][rows.id]['techs']['demand_electricity'] = {'constraints':{'resource':'file=power.csv'}}


        if 'heat' in sectors:
            dict_file['locations'][rows.id]['techs']['demand_heat'] = {'constraints':{'resource':'file=heat.csv'}}
            for add_tech in ['supply_gas','supply_biogas', 'heat_pump_air']:
                dict_file['locations'][rows.id]['techs'][add_tech] = None

        if 'iron and steel' in sectors:
            dict_file['locations'][rows.id]['techs']['demand_hydrogen'] = {'constraints':{'resource':'file=hydrogen.csv'}}
            for add_tech in ['electrolyser','fuel_cell', 'h2_storage']:
                dict_file['locations'][rows.id]['techs'][add_tech] = None

        for tech_dic in [{'tech':'power_plants'}, {'hydro_tech':'hydro_capacity'}]:
            tech_coords, tech_var = tech_dic.popitem()
            for tech in ds_regions.coords[tech_coords].values:
                installed_capacity = ds_regions[tech_var].loc[rows.nuts_2s,tech].values.item()
                if tech != 'Hydro':
                    dict_file['locations'][rows.id]['techs'][tech.lower().replace(' ','_')] = None
                    if tech in vre_dic.keys():
                        dict_file['locations'][rows.id]['techs'][tech.lower().replace(' ','_')] = {'constraints':{'energy_cap_min':installed_capacity}}
                        area_max = ds_regions[vre_dic.get(tech)[0]].loc[rows.nuts_2s].values.item()
                        if tech == 'Solar':
                            area_max = area_max + ds_regions['utility_pv'].loc[rows.nuts_2s].values.item()
                        if area_max*vre_dic.get(tech)[1] < installed_capacity:
                            area_max = (installed_capacity / vre_dic.get(tech)[1])+1
                        dict_file['locations'][rows.id]['techs'][tech.lower().replace(' ','_')]['constraints']['resource_area_max'] = area_max
                    if tech in ['HPHS', 'HDAM']:
                        storage_capacity = ds_regions['hydro_storage'].loc[rows.nuts_2s,tech].values.item()
                        if storage_capacity == 0:
                            storage_capacity = 6*installed_capacity
                        dict_file['locations'][rows.id]['techs'][tech.lower().replace(' ','_')] = {'constraints':{'energy_cap_equals':installed_capacity}}
                        dict_file['locations'][rows.id]['techs'][tech.lower().replace(' ','_')]['constraints']['storage_cap_equals'] = storage_capacity
                    if tech in ['Combined cycle']:
                        dict_file['locations'][rows.id]['techs'][tech.lower().replace(' ','_')] = {'constraints':{'energy_cap_min':installed_capacity}}
                    if tech in ['HROR']:
                        dict_file['locations'][rows.id]['techs'][tech.lower().replace(' ','_')] = {'constraints':{'energy_cap_equals':installed_capacity}}

        storages = ['battery', 'hydrogen']
        if 'iron and steel' in sectors:
            storages.remove('hydrogen')

        for techs in storages:
            dict_file['locations'][rows.id]['techs'][techs] = None

        for j, rows_2 in regions_geo.iterrows():
            g1_geo = rows.geometry.buffer(0.0001)
            g2_geo = rows_2.geometry.buffer(0.0001)
            fr = rows.geometry.centroid
            to = rows_2.geometry.centroid
            length = int(distance.distance((fr.y,fr.x), (to.y,to.x)).km*1.25)
            if g1_geo.intersects(g2_geo) == True and length not in line_lenght:
                line_lenght.append(length)
                trans_dic = {'techs':{'ac_transmission': {'distance':length/1e2} }}
                dict_file['links']['{},{}'.format(rows.id, rows_2.id)] = trans_dic

    for i,rows in dc_links.iterrows():
        rows_filtr_from = regions_geo[regions_geo.nuts_2s.astype(str).str.contains(rows['from'],regex=True)]
        rows_filtr_to = regions_geo[regions_geo.nuts_2s.astype(str).str.contains(rows['to'],regex=True)]
        if (rows_filtr_to.empty or rows_filtr_from.empty) == False:
            fr_index = rows_filtr_from.id.values[0]
            to_index = rows_filtr_to.id.values[0]
            if rows_filtr_from.iloc[0].id != rows_filtr_to.iloc[0].id:
                trans_dic = {'techs':{'dc_transmission': {'constraints':{'energy_cap_equals':rows.capacity},'distance':rows.length/1e2} }}
                dict_file['links']['{},{}'.format(fr_index, to_index)] = trans_dic


    with open(r'calliope_model/model_config/locations.yaml', 'w') as file:
        documents = yaml.dump(dict_file, file)

def create_model_yaml(self, regions_geo, sectors, op_mode, co2_cap_factor):
    ds_regions = self.ds_regions
    pop_factor = ds_regions["population"].sum()/500.9e6
    year = self.year

    dict_file = {'import': {}, 'model': {}, 'run': {}}
    dict_file['import'] = ['model_config/techs_elec.yaml','model_config/locations.yaml', 'scenarios.yaml']


    dict_file['model']['name'] = 'ESES model'
    dict_file['model']['calliope_version'] = '0.6.5'
    dict_file['model']['timeseries_data_path'] = 'timeseries_data'
    dict_file['model']['subset_time'] = ['{}-01-01'.format(year), '{}-12-31'.format(year)]
    # dict_file['model']['time'] = {'function':'resample','function_options':{'resolution': '3H'}}

    dict_file['run']['solver'] = 'cbc'
    dict_file['run']['ensure_feasibility'] = 'false'
    dict_file['run']['bigM'] = 1e9
    dict_file['run']['zero_threshold'] = 1e-15
    dict_file['run']['mode'] = op_mode
    dict_file['run']['objective_options.cost_class'] = {'monetary': 1}

    dict_file['group_constraints'] = {}
    if op_mode == 'plan':
        # for i,rows in regions_geo.iterrows():
            # dict_file['group_constraints']['{}_land_area_cap'.format(rows.id)] = {}
            # dict_file['group_constraints']['{}_land_area_cap'.format(rows.id)]['techs'] =['wind','solar']
            # dict_file['group_constraints']['{}_land_area_cap'.format(rows.id)]['locs'] = [rows.id]
            # area_max = ds_regions['land_area'].loc[rows.nuts_2s].values.item()
            # wind_solar_area = (ds_regions['power_plants'].loc[rows.nuts_2s,'Solar'].values.item() / tech_area.get('Solar')) +  (ds_regions['power_plants'].loc[rows.nuts_2s,'Wind'].values.item() / tech_area.get('Wind'))
            # if area_max < wind_solar_area:
            #     area_max = wind_solar_area + 1
            # dict_file['group_constraints']['{}_land_area_cap'.format(rows.id)]['resource_area_max'] = area_max

        # CO2 emissions constraint
        if co2_cap_factor!=None:
            c02_vol = sum([pr.get_metadata(c,'co_2_1990') for c in self.countries])*1e6
            dict_file['group_constraints']['systemwide_co2_cap'] = {'cost_max':{'co2':co2_cap_factor*c02_vol}}

    else:
        dict_file['import'] = ['model_config/techs_elec.yaml','model_config/locations.yaml']
        dict_file['run']['operation'] = {'horizon': 48, 'window': 24}


    # biogas cap
    biogas_cap = float(pop_factor) * 116.4e6
    constraint = {'techs':['supply_biogas'],'carrier_prod_max':{'gas':biogas_cap}}
    dict_file['group_constraints']['systemwide_biogas_cap'] = constraint

    if 'heat' in sectors:
        dict_file['import'] = ['model_config/techs_elec_heat.yaml','model_config/locations.yaml', 'scenarios.yaml']

    if 'iron and steel' in sectors:
        dict_file['import'] = ['model_config/techs_elec_heat_h2.yaml','model_config/locations.yaml', 'scenarios.yaml']

    with open(r'calliope_model/model.yaml', 'w') as file:
        documents = yaml.dump(dict_file, file)
