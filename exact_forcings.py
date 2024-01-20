import xarray
import s3fs
from pathlib import Path
import geopandas as gpd
import xarray as xr
from pathlib import Path
import multiprocessing
from functools import partial
import pandas as pd
import json
from exactextract import exact_extract
from datetime import datetime

def get_zarr_stores(start_time, end_time):
    forcing_zarr_files = ["lwdown", "precip", "psfc", "q2d", "swdown", "t2d", "u2d", "v2d"]
    urls = [f"s3://noaa-nwm-retrospective-3-0-pds/CONUS/zarr/forcing/{f}.zarr" for f in forcing_zarr_files]
    s3_file_stores = [s3fs.S3Map(url, s3=s3fs.S3FileSystem(anon=True)) for url in urls]
    time_slice = slice(start_time, end_time)
    lazy_store = xarray.open_mfdataset(s3_file_stores, combine='by_coords', parallel=True, engine='zarr')
    lazy_store = lazy_store.sel(time=time_slice)
    return lazy_store

def get_gdf(geopackage_path, projection):
    gdf = gpd.read_file(geopackage_path, layer='divides')
    gdf = gdf.to_crs(projection)
    return gdf

def clip_stores_to_catchments(store, bounds):
     # clip stores to catchments
    clipped_store = store.sel(x=slice(bounds[0], bounds[2]), y=slice(bounds[1], bounds[3]))
    return clipped_store

def compute_store(stores):
    # compute the store
    merged_data = stores.compute()
    # save the store locally
    merged_data.to_netcdf('merged_data.nc')
    return merged_data

def get_grid_file(grid_file):
    try:
        ds = xr.open_dataset(grid_file,engine='netcdf4')
        try:
            projection = ds.crs.esri_pe_string
        except:
            try:
                projection = ds.ProjectionCoordinateSystem.esri_pe_string
            except:
                raise Exception(f'\n\nCan\'t find projection!\n')
    except:
        raise Exception(f'\n\nThere\'s a problem with {grid_file}!\n')
    print(f'Projection: {projection}')
    return ds, projection

def compute_and_save(rasters, gdf, time, variable):
    raster=rasters[variable]
    #print(f'Computing {variable} for {time}')
    results = exact_extract(raster, gdf, ['mean'], include_cols=['divide_id'], output='pandas')
    # make divide_id the index
    results = results.set_index('divide_id')
    # make the column name the variable name
    results.columns = [variable]
    results.to_csv(f'temp/{variable}_{time}.csv')
    return results

def compute_zonal_stats(gdf, merged_data):
    # desired output format
    # one file per catchment
    # csv with columns: time, LWDOWN, PSFC, Q2D, RAINRATE, SWDOWN, T2D, U2D, V2D
    # exact extract works best with one timestep, lots of polygons
    print('Computing zonal stats')
    print(merged_data)
    all_dfs = []
    with multiprocessing.Pool() as pool:
        for time in merged_data.time.values:
            raster = merged_data.sel(time=time)
            timestep_result = pool.map(partial(compute_and_save,raster, gdf, time), ['LWDOWN', 'PSFC','Q2D', 'RAINRATE', 'SWDOWN', 'T2D', 'U2D', 'V2D'])
            timestep_result = pd.concat(timestep_result, axis=1)
            timestring = datetime.strftime(pd.to_datetime(str(time)), '%Y%m%d_%H%M')
            timestep_result.to_csv(f'temp/by_time/{timestring}.csv')
            all_dfs.append(timestep_result)
        # merge the results dicts into one dict      
    # use awk to pivot the csvs to be one per catchment
    # awk -F, '{print > "temp/by_cat/"$1".csv"}' temp/by_time/*.csv
    for df in all_dfs:
        pd.concat(all_dfs, axis=1).to_csv('results.csv')
        
        
    return "errr"


def create_forcings(start_time, end_time, wb_id):
    data_directory = Path(__file__).parent / 'output' / wb_id / 'config'
    geopackage_path = data_directory / f'{wb_id}_subset.gpkg'
    template_file = Path(__file__).parent / 'data_sources' / 'template.nc'
    
    #lazy_store = get_zarr_stores(start_time, end_time)
    print('Got zarr stores')
    df, projection = get_grid_file(template_file)
    print('Got grid file')
    gdf = get_gdf(geopackage_path, projection)
    print('Got gdf')
    #clipped_store = clip_stores_to_catchments(lazy_store, gdf.total_bounds)
    print('Clipped stores')
    #merged_data = compute_store(clipped_store)
    print('Computed store')
    merged_data = xr.open_dataset('merged_data.nc')
    compute_zonal_stats(gdf, merged_data)
    #save_data_as_csv(results, start_time, end_time, wb_id, gdf)


if __name__ == '__main__':
    start_time = '2010-01-01 00:00'
    end_time = '2010-01-01 05:00'
    wb_id = 'wb-1643991'
    create_forcings(start_time, end_time, wb_id)
